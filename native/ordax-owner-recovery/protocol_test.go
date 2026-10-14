package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"testing"
)

func validJobMessage(capability string, payload []byte) map[string]any {
	digest := sha256.Sum256(payload)
	return map[string]any{
		"type": "job",
		"job": map[string]any{
			"job_id":                "11111111-1111-4111-8111-111111111111",
			"capability":            capability,
			"payload_canonical_b64": base64.StdEncoding.EncodeToString(payload),
			"payload_sha256":        hex.EncodeToString(digest[:]),
			"effect_id":             "22222222-2222-4222-8222-222222222222",
			"attempt_id":            "33333333-3333-4333-8333-333333333333",
			"lease_id":              "44444444-4444-4444-8444-444444444444",
			"execution_epoch":       1,
			"agent_instance_id":     "55555555-5555-4555-8555-555555555555",
			"boot_id":               "66666666-6666-4666-8666-666666666666",
		},
	}
}

func TestDecodeJobValidatesDigestIdentityAndArguments(t *testing.T) {
	message := validJobMessage("agent.owner.snapshot", []byte(`{}`))
	if _, _, err := decodeJob(message, "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666"); err != nil {
		t.Fatalf("valid job rejected: %v", err)
	}
	job := message["job"].(map[string]any)
	job["payload_sha256"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	if _, _, err := decodeJob(message, "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666"); err == nil {
		t.Fatal("digest mismatch accepted")
	}

	message = validJobMessage("agent.owner.snapshot", []byte(`{"path":"/etc/shadow"}`))
	if _, _, err := decodeJob(message, "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666"); err == nil {
		t.Fatal("remote arguments accepted")
	}

	message = validJobMessage("terminal.exec", []byte(`{}`))
	if _, _, err := decodeJob(message, "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666"); err == nil {
		t.Fatal("generic shell capability accepted")
	}
}

func TestCanonicalResultStable(t *testing.T) {
	encoded, digest, err := canonicalResult(actionResult{OK: true, Summary: "ok", Data: map[string]any{"b": 2, "a": 1}})
	if err != nil {
		t.Fatal(err)
	}
	want := `{"data":{"a":1,"b":2},"ok":true,"summary":"ok"}`
	if string(encoded) != want {
		t.Fatalf("canonical result mismatch: %s", encoded)
	}
	sum := sha256.Sum256([]byte(want))
	if digest != hex.EncodeToString(sum[:]) {
		t.Fatal("canonical digest mismatch")
	}
}
