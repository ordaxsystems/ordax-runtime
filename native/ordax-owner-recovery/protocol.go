package main

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const protocolName = "cloudflare-v3"

var version = "dev"

type jobEnvelope struct {
	JobID               string `json:"job_id"`
	Capability          string `json:"capability"`
	PayloadCanonicalB64 string `json:"payload_canonical_b64"`
	PayloadSHA256       string `json:"payload_sha256"`
	EffectID            string `json:"effect_id"`
	AttemptID           string `json:"attempt_id"`
	LeaseID             string `json:"lease_id"`
	ExecutionEpoch      int64  `json:"execution_epoch"`
	AgentInstanceID     string `json:"agent_instance_id"`
	BootID              string `json:"boot_id"`
}

type executionContext struct {
	JobID           string `json:"job_id"`
	EffectID        string `json:"effect_id"`
	AttemptID       string `json:"attempt_id"`
	LeaseID         string `json:"lease_id"`
	ExecutionEpoch  int64  `json:"execution_epoch"`
	AgentInstanceID string `json:"agent_instance_id"`
	BootID          string `json:"boot_id"`
}

type actionResult struct {
	OK      bool           `json:"ok"`
	Summary string         `json:"summary"`
	Data    map[string]any `json:"data"`
}

type client struct {
	cfg             config
	conn            *websocket.Conn
	agentInstanceID string
	bootID          string
	http            *http.Client
	writeMu         sync.Mutex
	pendingMu       sync.Mutex
	pending         map[string]chan map[string]any
	jobs            chan map[string]any
	serverPing      chan struct{}
	readErr         chan error
}

func newClient(cfg config) (*client, error) {
	agentID, err := randomUUID()
	if err != nil {
		return nil, err
	}
	bootID, err := randomUUID()
	if err != nil {
		return nil, err
	}
	return &client{
		cfg:             cfg,
		agentInstanceID: agentID,
		bootID:          bootID,
		http:            &http.Client{Timeout: 20 * time.Second},
		pending:         make(map[string]chan map[string]any),
		jobs:            make(chan map[string]any, 8),
		serverPing:      make(chan struct{}, 1),
		readErr:         make(chan error, 1),
	}, nil
}

func randomUUID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		b[0:4], b[4:6], b[6:8], b[8:10], b[10:16]), nil
}

func (c *client) wsURL() (string, error) {
	parsed, err := url.Parse(c.cfg.ControlPlane)
	if err != nil {
		return "", err
	}
	parsed.Scheme = "wss"
	parsed.Path = strings.TrimRight(parsed.Path, "/") + "/v3/device/ws"
	query := parsed.Query()
	query.Set("device_id", c.cfg.DeviceID)
	parsed.RawQuery = query.Encode()
	return parsed.String(), nil
}

func (c *client) connect() error {
	endpoint, err := c.wsURL()
	if err != nil {
		return err
	}
	headers := http.Header{}
	headers.Set("X-Ordax-Device-Token", c.cfg.DeviceToken)
	headers.Set("X-Ordax-Agent-Instance", c.agentInstanceID)
	headers.Set("X-Ordax-Boot-Id", c.bootID)
	headers.Set("User-Agent", "OrdaX-Owner-Recovery/"+version)
	dialer := websocket.Dialer{HandshakeTimeout: 10 * time.Second, EnableCompression: false}
	conn, response, err := dialer.Dial(endpoint, headers)
	if err != nil {
		if response != nil {
			return fmt.Errorf("websocket connect rejected: HTTP %d", response.StatusCode)
		}
		return fmt.Errorf("websocket connect failed: %w", err)
	}
	conn.SetReadLimit(2 * 1024 * 1024)
	c.conn = conn

	_ = conn.SetReadDeadline(time.Now().Add(10 * time.Second))
	var hello map[string]any
	if err := conn.ReadJSON(&hello); err != nil {
		c.close()
		return err
	}
	_ = conn.SetReadDeadline(time.Time{})
	if hello["type"] != "hello" || hello["device_id"] != c.cfg.DeviceID || hello["protocol"] != protocolName {
		c.close()
		return errors.New("control plane hello identity/protocol mismatch")
	}
	c.pendingMu.Lock()
	c.pending = make(map[string]chan map[string]any)
	c.pendingMu.Unlock()
	c.jobs = make(chan map[string]any, 8)
	c.serverPing = make(chan struct{}, 1)
	c.readErr = make(chan error, 1)
	go c.readPump(conn)
	return c.heartbeat()
}

func (c *client) close() {
	if c.conn != nil {
		_ = c.conn.Close()
		c.conn = nil
	}
}

func (c *client) writeJSON(value any, timeout time.Duration) error {
	if c.conn == nil {
		return errors.New("websocket is not connected")
	}
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	_ = c.conn.SetWriteDeadline(time.Now().Add(timeout))
	return c.conn.WriteJSON(value)
}

func (c *client) readPump(conn *websocket.Conn) {
	for {
		var message map[string]any
		if err := conn.ReadJSON(&message); err != nil {
			select {
			case c.readErr <- err:
			default:
			}
			return
		}
		switch message["type"] {
		case "ack":
			requestID, _ := message["request_id"].(string)
			c.pendingMu.Lock()
			ch := c.pending[requestID]
			c.pendingMu.Unlock()
			if ch != nil {
				select {
				case ch <- message:
				default:
				}
			}
		case "job":
			select {
			case c.jobs <- message:
			default:
				select {
				case c.readErr <- errors.New("remote job queue overflow"):
				default:
				}
				return
			}
		case "ping":
			select {
			case c.serverPing <- struct{}{}:
			default:
			}
		default:
			select {
			case c.readErr <- fmt.Errorf("unexpected control-plane message type: %v", message["type"]):
			default:
			}
			return
		}
	}
}

func (c *client) rpc(kind string, payload map[string]any, timeout time.Duration) error {
	requestID, err := randomUUID()
	if err != nil {
		return err
	}
	ack := make(chan map[string]any, 1)
	c.pendingMu.Lock()
	c.pending[requestID] = ack
	c.pendingMu.Unlock()
	defer func() {
		c.pendingMu.Lock()
		delete(c.pending, requestID)
		c.pendingMu.Unlock()
	}()

	body := map[string]any{"type": kind, "request_id": requestID}
	for key, value := range payload {
		body[key] = value
	}
	if err := c.writeJSON(body, 10*time.Second); err != nil {
		return err
	}
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case message := <-ack:
		ok, _ := message["ok"].(bool)
		if !ok {
			return fmt.Errorf("control plane rejected %s: %v", kind, message["error"])
		}
		return nil
	case err := <-c.readErr:
		return err
	case <-timer.C:
		return fmt.Errorf("control plane %s acknowledgement timed out", kind)
	}
}

func decodeJob(message map[string]any, agentInstanceID, bootID string) (jobEnvelope, map[string]any, error) {
	rawJob, ok := message["job"]
	if !ok {
		return jobEnvelope{}, nil, errors.New("job envelope missing")
	}
	bytes, err := json.Marshal(rawJob)
	if err != nil {
		return jobEnvelope{}, nil, err
	}
	var job jobEnvelope
	if err := json.Unmarshal(bytes, &job); err != nil {
		return jobEnvelope{}, nil, err
	}
	for _, id := range []string{job.JobID, job.EffectID, job.AttemptID, job.LeaseID, job.AgentInstanceID, job.BootID} {
		if !uuidPattern.MatchString(id) {
			return jobEnvelope{}, nil, errors.New("job execution identity is invalid")
		}
	}
	if job.ExecutionEpoch < 1 || job.AgentInstanceID != agentInstanceID || job.BootID != bootID {
		return jobEnvelope{}, nil, errors.New("job execution context does not match this agent")
	}
	if len(job.PayloadSHA256) != 64 {
		return jobEnvelope{}, nil, errors.New("job payload digest is invalid")
	}
	payloadBytes, err := base64.StdEncoding.Strict().DecodeString(job.PayloadCanonicalB64)
	if err != nil {
		return jobEnvelope{}, nil, errors.New("job payload is not valid base64")
	}
	digest := sha256.Sum256(payloadBytes)
	if hex.EncodeToString(digest[:]) != strings.ToLower(job.PayloadSHA256) {
		return jobEnvelope{}, nil, errors.New("job payload digest mismatch")
	}
	var payload map[string]any
	if err := json.Unmarshal(payloadBytes, &payload); err != nil || payload == nil {
		return jobEnvelope{}, nil, errors.New("job payload must be a JSON object")
	}
	if len(payload) != 0 {
		return jobEnvelope{}, nil, errors.New("owner recovery actions do not accept remote arguments")
	}
	if !capabilityAllowed(job.Capability) {
		return jobEnvelope{}, nil, fmt.Errorf("capability %q is not allowed", job.Capability)
	}
	return job, payload, nil
}

func contextMap(job jobEnvelope) map[string]any {
	return map[string]any{
		"job_id":            job.JobID,
		"effect_id":         job.EffectID,
		"attempt_id":        job.AttemptID,
		"lease_id":          job.LeaseID,
		"execution_epoch":   job.ExecutionEpoch,
		"agent_instance_id": job.AgentInstanceID,
		"boot_id":           job.BootID,
	}
}

func canonicalResult(result actionResult) ([]byte, string, error) {
	if result.Data == nil {
		result.Data = map[string]any{}
	}
	body := map[string]any{"data": result.Data, "ok": result.OK, "summary": result.Summary}
	encoded, err := json.Marshal(body)
	if err != nil {
		return nil, "", err
	}
	digest := sha256.Sum256(encoded)
	return encoded, hex.EncodeToString(digest[:]), nil
}

func (c *client) persistAndReport(job jobEnvelope, result actionResult) error {
	encoded, digest, err := canonicalResult(result)
	if err != nil {
		return err
	}
	var resultObject map[string]any
	if err := json.Unmarshal(encoded, &resultObject); err != nil {
		return err
	}
	reportID, err := randomUUID()
	if err != nil {
		return err
	}
	status := "failed"
	exitCode := 1
	var errorCode any = "owner_recovery_action_failed"
	if result.OK {
		status, exitCode, errorCode = "succeeded", 0, nil
	}
	report := contextMap(job)
	report["report_id"] = reportID
	report["status"] = status
	report["exit_code"] = exitCode
	report["result"] = resultObject
	report["result_sha256"] = digest
	report["error_code"] = errorCode

	outbox := filepath.Join(c.cfg.StateDir, "outbox")
	if err := os.MkdirAll(outbox, 0o700); err != nil {
		return err
	}
	path := filepath.Join(outbox, reportID+".json")
	if err := writeJSONAtomic(path, report, 0o600); err != nil {
		return err
	}
	if err := c.rpc("report", report, 30*time.Second); err != nil {
		return err
	}
	return os.Remove(path)
}

func writeJSONAtomic(path string, value any, mode os.FileMode) error {
	encoded, err := json.Marshal(value)
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(encoded, '\n'), mode); err != nil {
		return err
	}
	if err := os.Chmod(tmp, mode); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, path)
}

func (c *client) recoverPendingReports() error {
	dir := filepath.Join(c.cfg.StateDir, "outbox")
	entries, err := os.ReadDir(dir)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		path := filepath.Join(dir, entry.Name())
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		var report map[string]any
		if err := json.Unmarshal(data, &report); err != nil {
			return fmt.Errorf("invalid pending report %s", entry.Name())
		}
		body, _ := json.Marshal(report)
		request, err := http.NewRequest(http.MethodPost, c.cfg.ControlPlane+"/v3/device/recover-report", strings.NewReader(string(body)))
		if err != nil {
			return err
		}
		request.Header.Set("content-type", "application/json")
		request.Header.Set("X-Ordax-Device-Id", c.cfg.DeviceID)
		request.Header.Set("X-Ordax-Device-Token", c.cfg.DeviceToken)
		request.Header.Set("X-Ordax-Recovery-Agent-Instance", c.agentInstanceID)
		request.Header.Set("X-Ordax-Recovery-Boot-Id", c.bootID)
		response, err := c.http.Do(request)
		if err != nil {
			return err
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			_ = response.Body.Close()
			return fmt.Errorf("pending report recovery rejected: HTTP %d", response.StatusCode)
		}
		// HTTP success is not an acknowledgement. Do not discard the durable
		// outbox report unless the server explicitly confirms persistence.
		var acknowledgement struct {
			OK bool `json:"ok"`
		}
		decoder := json.NewDecoder(io.LimitReader(response.Body, 4096))
		decodeErr := decoder.Decode(&acknowledgement)
		closeErr := response.Body.Close()
		if decodeErr != nil || closeErr != nil || !acknowledgement.OK {
			return errors.New("pending report recovery was not acknowledged")
		}
		if err := os.Remove(path); err != nil {
			return err
		}
	}
	return nil
}
