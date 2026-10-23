package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"syscall"
	"time"
)

// An idle iteration performs one heartbeat and one SQL-backed claim. Grow the
// quiet interval to reduce database traffic while keeping action pickup bounded.
const canonicalOwnerIdlePollInterval = 12 * time.Second
const canonicalOwnerIdlePollMaxInterval = 30 * time.Second

// runCanonicalOwnerRecovery owns the *entire* execution lifecycle. It must
// never connect to the legacy D1 WebSocket or process its separate outbox.
func runCanonicalOwnerRecovery(cfg config) error {
	c, err := newOwnerSession(cfg)
	if err != nil {
		return err
	}
	transport := canonicalDeviceTransport{baseURL: cfg.ControlPlane, deviceID: cfg.DeviceID, deviceToken: cfg.DeviceToken, httpClient: c.http}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(stop)
	failures := 0
	idleDelay := canonicalOwnerIdlePollInterval
	for {
		select {
		case <-stop:
			return nil
		default:
		}
		claimed, err := canonicalOwnerIteration(c, transport)
		if err != nil {
			failures++
			if !sleepOrStop(stop, canonicalOwnerRetryDelay(failures)) {
				return nil
			}
			continue
		}
		failures = 0
		if claimed {
			idleDelay = canonicalOwnerNextIdlePollDelay(idleDelay, true)
		}
		if !sleepOrStop(stop, idleDelay) {
			return nil
		}
		if !claimed {
			idleDelay = canonicalOwnerNextIdlePollDelay(idleDelay, false)
		}
	}
}

func canonicalOwnerNextIdlePollDelay(current time.Duration, claimed bool) time.Duration {
	if claimed {
		return canonicalOwnerIdlePollInterval
	}
	if current < canonicalOwnerIdlePollInterval {
		return canonicalOwnerIdlePollInterval
	}
	if current >= canonicalOwnerIdlePollMaxInterval {
		return canonicalOwnerIdlePollMaxInterval
	}
	next := current * 2
	if next > canonicalOwnerIdlePollMaxInterval {
		return canonicalOwnerIdlePollMaxInterval
	}
	return next
}

// Backoff must not multiply errors if Hyperdrive is temporarily unavailable.
func canonicalOwnerRetryDelay(failures int) time.Duration {
	if failures <= 1 {
		return 5 * time.Second
	}
	if failures == 2 {
		return 10 * time.Second
	}
	if failures == 3 {
		return 20 * time.Second
	}
	return 30 * time.Second
}

func canonicalOwnerIteration(c *ownerSession, transport canonicalDeviceTransport) (bool, error) {
	return canonicalOwnerIterationWithExecutor(c, transport, executeCapability)
}

func canonicalOwnerIterationWithExecutor(c *ownerSession, transport canonicalDeviceTransport, execute func(config, string, string) actionResult) (bool, error) {
	// Recover exact committed receipts before claiming any new action.
	if err := replayCanonicalPendingReports(c.cfg.StateDir, transport); err != nil {
		return false, err
	}
	capabilities := allowedCapabilities()
	sort.Strings(capabilities)
	compact, _ := json.Marshal(capabilities)
	digest := sha256.Sum256(compact)
	if _, err := transport.post("heartbeat", map[string]any{
		"status": "online", "runtime_kind": "ordax-os", "agent_version": version,
		"capabilities_digest": hex.EncodeToString(digest[:]),
	}); err != nil {
		return false, err
	}
	job, err := claimCanonicalOwnerJob(transport, c.agentInstanceID, c.bootID)
	if err != nil {
		return false, err
	}
	if job == nil {
		return false, nil
	}
	ctx := contextMap(*job)
	ctx["runtime_kind"] = "ordax-os"
	ctx["agent_version"] = version
	if _, err = transport.post("start", ctx); err != nil {
		return false, err
	}
	// A completed local mutation must be reported even if lease renewal fails.
	// The server will reject a superseded epoch instead of replaying the action.
	resultCh := make(chan actionResult, 1)
	go func() { resultCh <- execute(c.cfg, job.Capability, job.EffectID) }()
	renew := time.NewTicker(30 * time.Second)
	defer renew.Stop()
	var result actionResult
	for {
		select {
		case result = <-resultCh:
			goto finished
		case <-renew.C:
			lease := contextMap(*job)
			lease["lease_seconds"] = 120
			if _, err = transport.post("lease", lease); err != nil {
				// The work may still be running. Do not issue another claim.
				result = <-resultCh
				goto finished
			}
		}
	}
finished:
	encoded, digestHex, err := canonicalResult(result)
	if err != nil {
		return false, err
	}
	var resultObject map[string]any
	if err = json.Unmarshal(encoded, &resultObject); err != nil {
		return false, err
	}
	reportID, err := randomUUID()
	if err != nil {
		return false, err
	}
	report := contextMap(*job)
	report["report_id"] = reportID
	report["result"] = resultObject
	report["result_sha256"] = digestHex
	report["status"] = "failed"
	report["error_code"] = "owner_recovery_action_failed"
	if result.OK {
		report["status"] = "succeeded"
		report["error_code"] = nil
	}
	outbox := filepath.Join(c.cfg.StateDir, "postgres-outbox")
	if err = os.MkdirAll(outbox, 0700); err != nil {
		return false, err
	}
	path := filepath.Join(outbox, reportID+".json")
	if err = writeJSONAtomic(path, report, 0600); err != nil {
		return false, err
	}
	wire, err := canonicalRecoveryWire(report)
	if err != nil {
		return false, err
	}
	if _, err = transport.post("report", wire); err != nil {
		return false, err
	}
	if err = os.Remove(path); err != nil {
		return false, err
	}
	return true, nil
}
