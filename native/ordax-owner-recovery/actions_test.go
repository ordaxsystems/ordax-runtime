package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCapabilityAllowlistIsNarrow(t *testing.T) {
	allowed := allowedCapabilities()
	if len(allowed) != 5 {
		t.Fatalf("expected 5 capabilities, got %d", len(allowed))
	}
	forbidden := []string{"terminal.exec", "git.command", "computer.text_write", "agent.shell", "artifact.upload"}
	for _, value := range forbidden {
		if capabilityAllowed(value) {
			t.Fatalf("forbidden capability %q became allowed", value)
		}
	}
}

func TestReadSecretRejectsLoosePermissionsAndSymlink(t *testing.T) {
	root := t.TempDir()
	secret := filepath.Join(root, "token")
	if err := os.WriteFile(secret, []byte("abcdefghijklmnopqrstuvwxyz0123456789"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := readSecretFile(secret); err == nil {
		t.Fatal("expected loose permissions to be rejected")
	}
	if err := os.Chmod(secret, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := readSecretFile(secret); err != nil {
		t.Fatalf("expected 0600 secret to pass: %v", err)
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(secret, link); err != nil {
		t.Fatal(err)
	}
	if _, err := readSecretFile(link); err == nil {
		t.Fatal("expected symlink secret to be rejected")
	}
}

func TestGuardedMutationBlocksReplay(t *testing.T) {
	cfg := config{StateDir: t.TempDir()}
	effect := "01234567-89ab-4cde-8fab-0123456789ab"
	calls := 0
	first := guardedMutation(cfg, effect, "agent.owner.pull", func() actionResult {
		calls++
		return actionResult{OK: true, Summary: "ok", Data: map[string]any{}}
	})
	if !first.OK {
		t.Fatalf("first mutation failed: %#v", first)
	}
	second := guardedMutation(cfg, effect, "agent.owner.pull", func() actionResult {
		calls++
		return actionResult{OK: true, Summary: "bad", Data: map[string]any{}}
	})
	if second.OK {
		t.Fatal("replayed mutation should be blocked")
	}
	if calls != 1 {
		t.Fatalf("mutation executed %d times", calls)
	}
}
