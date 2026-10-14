package main

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var uuidPattern = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$`)

type config struct {
	StateDir      string
	Worktree      string
	ControlPlane  string
	DeviceID      string
	DeviceToken   string
	GitBin        string
	PullHelper    string
	RunHelper     string
	GuardianPID   string
	GuardianLog   string
	RuntimeState  string
	NetworkLog    string
	CurrentCommit string
	HelpersCommit string
}

func loadConfig() (config, error) {
	stateDir := envOr("ORDAX_OWNER_REMOTE_STATE_DIR", "/state/ordax/owner-remote")
	cfg := config{
		StateDir:      stateDir,
		Worktree:      envOr("ORDAX_WORKTREE", "/workspace/ordax"),
		GitBin:        envOr("ORDAX_GIT_BIN", "/usr/bin/git"),
		PullHelper:    envOr("ORDAX_PULL_HELPER", "/usr/local/bin/ordax-pull"),
		RunHelper:     envOr("ORDAX_RUN_HELPER", "/usr/local/bin/ordax-run"),
		GuardianPID:   envOr("ORDAX_GUARDIAN_PID_FILE", "/run/ordax-update/guardian.pid"),
		GuardianLog:   envOr("ORDAX_GUARDIAN_LOG", "/run/ordax-update/guardian.log"),
		RuntimeState:  envOr("ORDAX_RUNTIME_STATE", "/run/ordax-update/state.json"),
		NetworkLog:    envOr("ORDAX_NETWORK_LOG", "/state/ordax/boot-network.log"),
		CurrentCommit: envOr("ORDAX_CURRENT_COMMIT_FILE", "/state/ordax/current-commit"),
		HelpersCommit: envOr("ORDAX_HELPERS_COMMIT_FILE", "/state/ordax/dev-helpers-sha"),
	}

	var err error
	cfg.ControlPlane, err = envOrFile("ORDAX_CONTROL_PLANE_URL", filepath.Join(stateDir, "control-plane-url"))
	if err != nil {
		return config{}, err
	}
	parsed, err := url.Parse(cfg.ControlPlane)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return config{}, errors.New("control plane must be an absolute HTTPS URL without userinfo/query/fragment")
	}
	cfg.ControlPlane = strings.TrimRight(cfg.ControlPlane, "/")

	cfg.DeviceID, err = envOrFile("ORDAX_DEVICE_ID", filepath.Join(stateDir, "device-id"))
	if err != nil {
		return config{}, err
	}
	if !uuidPattern.MatchString(cfg.DeviceID) {
		return config{}, errors.New("device id must be a canonical UUID")
	}

	tokenPath := envOr("ORDAX_DEVICE_TOKEN_FILE", filepath.Join(stateDir, "device-token"))
	cfg.DeviceToken, err = readSecretFile(tokenPath)
	if err != nil {
		return config{}, err
	}
	if len(cfg.DeviceToken) < 32 || len(cfg.DeviceToken) > 512 {
		return config{}, errors.New("device token length is invalid")
	}
	return cfg, nil
}

func envOr(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func envOrFile(envName, path string) (string, error) {
	if value := strings.TrimSpace(os.Getenv(envName)); value != "" {
		return value, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("%s is unavailable: %w", envName, err)
	}
	value := strings.TrimSpace(string(data))
	if value == "" {
		return "", fmt.Errorf("%s is empty", path)
	}
	return value, nil
}

func readSecretFile(path string) (string, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return "", fmt.Errorf("device token file is unavailable: %w", err)
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
		return "", errors.New("device token file must be a regular file")
	}
	if info.Mode().Perm()&0o077 != 0 {
		return "", errors.New("device token file must not be group/world accessible")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}
