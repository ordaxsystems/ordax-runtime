package main

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"
)

// Version is set to the exact source commit by the protected release workflow.
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

type actionResult struct {
	OK      bool           `json:"ok"`
	Summary string         `json:"summary"`
	Data    map[string]any `json:"data"`
}

// ownerSession contains only state required by canonical PostgreSQL transport.
type ownerSession struct {
	cfg             config
	agentInstanceID string
	bootID          string
	http            *http.Client
}

func newOwnerSession(cfg config) (*ownerSession, error) {
	agentID, err := randomUUID()
	if err != nil {
		return nil, err
	}
	bootID, err := randomUUID()
	if err != nil {
		return nil, err
	}
	return &ownerSession{
		cfg: cfg, agentInstanceID: agentID, bootID: bootID,
		http: &http.Client{Timeout: 20 * time.Second},
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
