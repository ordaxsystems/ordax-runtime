package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const maxCommandOutput = 128 * 1024

var capabilitySet = map[string]struct{}{
	"agent.owner.snapshot":      {},
	"agent.owner.git_status":    {},
	"agent.owner.pull":          {},
	"agent.owner.runtime_stop":  {},
	"agent.owner.runtime_start": {},
}

func allowedCapabilities() []string {
	return []string{
		"agent.owner.snapshot",
		"agent.owner.git_status",
		"agent.owner.pull",
		"agent.owner.runtime_stop",
		"agent.owner.runtime_start",
	}
}

func capabilityAllowed(value string) bool {
	_, ok := capabilitySet[value]
	return ok
}

func executeCapability(cfg config, capability, effectID string) actionResult {
	switch capability {
	case "agent.owner.snapshot":
		return snapshot(cfg)
	case "agent.owner.git_status":
		return gitStatus(cfg)
	case "agent.owner.pull":
		return guardedMutation(cfg, effectID, capability, func() actionResult { return pull(cfg) })
	case "agent.owner.runtime_stop":
		return guardedMutation(cfg, effectID, capability, func() actionResult { return runtimeStop(cfg) })
	case "agent.owner.runtime_start":
		return guardedMutation(cfg, effectID, capability, func() actionResult { return runtimeStart(cfg) })
	default:
		return actionResult{OK: false, Summary: "capability not allowed", Data: map[string]any{}}
	}
}

func guardedMutation(cfg config, effectID, capability string, fn func() actionResult) actionResult {
	if !uuidPattern.MatchString(effectID) {
		return actionResult{OK: false, Summary: "invalid effect identity", Data: map[string]any{}}
	}
	dir := filepath.Join(cfg.StateDir, "effects")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return actionResult{OK: false, Summary: "cannot initialize effect journal", Data: map[string]any{"error": err.Error()}}
	}
	path := filepath.Join(dir, effectID+".json")
	if _, err := os.Lstat(path); err == nil {
		return actionResult{OK: false, Summary: "mutation replay blocked", Data: map[string]any{"effect_id": effectID}}
	} else if !errors.Is(err, os.ErrNotExist) {
		return actionResult{OK: false, Summary: "cannot inspect effect journal", Data: map[string]any{"error": err.Error()}}
	}
	marker := map[string]any{"effect_id": effectID, "capability": capability, "started_at": time.Now().UTC().Format(time.RFC3339Nano)}
	if err := writeJSONAtomic(path, marker, 0o600); err != nil {
		return actionResult{OK: false, Summary: "cannot persist effect journal", Data: map[string]any{"error": err.Error()}}
	}
	result := fn()
	marker["finished_at"] = time.Now().UTC().Format(time.RFC3339Nano)
	marker["ok"] = result.OK
	marker["summary"] = result.Summary
	_ = writeJSONAtomic(path, marker, 0o600)
	return result
}

func snapshot(cfg config) actionResult {
	data := map[string]any{
		"guardian_log":   readBoundedFile(cfg.GuardianLog, 32*1024),
		"runtime_state":  readBoundedFile(cfg.RuntimeState, 16*1024),
		"network_log":    readBoundedFile(cfg.NetworkLog, 16*1024),
		"current_commit": readBoundedFile(cfg.CurrentCommit, 256),
		"helpers_commit": readBoundedFile(cfg.HelpersCommit, 256),
		"guardian":       guardianStatus(cfg),
		"mounts":         readBoundedFile("/proc/mounts", 32*1024),
	}
	return actionResult{OK: true, Summary: "owner development snapshot collected", Data: data}
}

func readBoundedFile(path string, max int64) map[string]any {
	result := map[string]any{"path": path, "present": false}
	info, err := os.Lstat(path)
	if err != nil {
		result["error"] = err.Error()
		return result
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
		result["error"] = "not a regular file"
		return result
	}
	file, err := os.Open(path)
	if err != nil {
		result["error"] = err.Error()
		return result
	}
	defer file.Close()
	start := int64(0)
	if info.Size() > max {
		start = info.Size() - max
	}
	if _, err := file.Seek(start, 0); err != nil {
		result["error"] = err.Error()
		return result
	}
	buf := make([]byte, max)
	n, err := file.Read(buf)
	if err != nil && n == 0 {
		result["error"] = err.Error()
		return result
	}
	result["present"] = true
	result["truncated"] = start > 0
	result["text"] = string(buf[:n])
	return result
}

func gitStatus(cfg config) actionResult {
	head := runFixed(10*time.Second, cfg.GitBin, []string{"-C", cfg.Worktree, "rev-parse", "HEAD"}, nil)
	status := runFixed(10*time.Second, cfg.GitBin, []string{"-C", cfg.Worktree, "status", "--porcelain=v1", "-b"}, nil)
	origin := runFixed(10*time.Second, cfg.GitBin, []string{"-C", cfg.Worktree, "remote", "get-url", "origin"}, nil)
	ok := head.ExitCode == 0 && status.ExitCode == 0 && origin.ExitCode == 0
	return actionResult{OK: ok, Summary: "owner Git status collected", Data: map[string]any{"head": head, "status": status, "origin": origin}}
}

type commandResult struct {
	ExitCode int    `json:"exit_code"`
	Output   string `json:"output"`
	TimedOut bool   `json:"timed_out"`
}

func runFixed(timeout time.Duration, executable string, args []string, env []string) commandResult {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	command := exec.CommandContext(ctx, executable, args...)
	if env != nil {
		command.Env = env
	}
	var output limitedBuffer
	command.Stdout = &output
	command.Stderr = &output
	err := command.Run()
	result := commandResult{ExitCode: 0, Output: output.String(), TimedOut: errors.Is(ctx.Err(), context.DeadlineExceeded)}
	if err != nil {
		result.ExitCode = 1
		if exit, ok := err.(*exec.ExitError); ok {
			result.ExitCode = exit.ExitCode()
		}
	}
	return result
}

type limitedBuffer struct{ bytes.Buffer }

func (b *limitedBuffer) Write(p []byte) (int, error) {
	original := len(p)
	remaining := maxCommandOutput - b.Len()
	if remaining > 0 {
		if len(p) > remaining {
			p = p[:remaining]
		}
		_, _ = b.Buffer.Write(p)
	}
	return original, nil
}

func pull(cfg config) actionResult {
	info, err := os.Lstat(cfg.PullHelper)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&0o111 == 0 || info.Mode()&os.ModeSymlink != 0 {
		return actionResult{OK: false, Summary: "ordax-pull helper is unavailable or unsafe", Data: map[string]any{}}
	}
	env := []string{"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME=/root", "GIT_TERMINAL_PROMPT=0", "ORDAX_WORKTREE=" + cfg.Worktree}
	result := runFixed(90*time.Second, cfg.PullHelper, nil, env)
	return actionResult{OK: result.ExitCode == 0 && !result.TimedOut, Summary: "ordax-pull completed", Data: map[string]any{"command": result}}
}

func guardianStatus(cfg config) map[string]any {
	result := map[string]any{"running": false}
	pid, err := guardianPID(cfg)
	if err != nil {
		result["detail"] = err.Error()
		return result
	}
	result["pid"] = pid
	result["running"] = processAlive(pid)
	cmdline, _ := os.ReadFile(fmt.Sprintf("/proc/%d/cmdline", pid))
	result["cmdline"] = strings.ReplaceAll(string(cmdline), "\x00", " ")
	result["owned"] = guardianProcessMatches(cfg, pid)
	return result
}

func guardianPID(cfg config) (int, error) {
	info, err := os.Lstat(cfg.GuardianPID)
	if err != nil {
		return 0, err
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
		return 0, errors.New("guardian pid file is unsafe")
	}
	raw, err := os.ReadFile(cfg.GuardianPID)
	if err != nil {
		return 0, err
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil || pid <= 1 {
		return 0, errors.New("guardian pid is invalid")
	}
	return pid, nil
}

func processAlive(pid int) bool {
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}

func guardianProcessMatches(cfg config, pid int) bool {
	raw, err := os.ReadFile(fmt.Sprintf("/proc/%d/cmdline", pid))
	if err != nil {
		return false
	}
	fields := strings.Split(string(raw), "\x00")
	expected := filepath.Join(cfg.Worktree, "system", "entrypoint")
	for _, field := range fields {
		if field == expected {
			return true
		}
	}
	return false
}

func runtimeStop(cfg config) actionResult {
	pid, err := guardianPID(cfg)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return actionResult{OK: true, Summary: "runtime already stopped", Data: map[string]any{}}
		}
		return actionResult{OK: false, Summary: "cannot resolve guardian", Data: map[string]any{"error": err.Error()}}
	}
	if !processAlive(pid) {
		return actionResult{OK: true, Summary: "runtime already stopped", Data: map[string]any{"pid": pid}}
	}
	if !guardianProcessMatches(cfg, pid) {
		return actionResult{OK: false, Summary: "guardian ownership proof failed", Data: map[string]any{"pid": pid}}
	}
	if err := syscall.Kill(pid, syscall.SIGTERM); err != nil {
		return actionResult{OK: false, Summary: "runtime stop failed", Data: map[string]any{"pid": pid, "error": err.Error()}}
	}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if !processAlive(pid) {
			return actionResult{OK: true, Summary: "runtime stopped", Data: map[string]any{"pid": pid}}
		}
		time.Sleep(250 * time.Millisecond)
	}
	return actionResult{OK: false, Summary: "runtime did not stop before deadline", Data: map[string]any{"pid": pid}}
}

func runtimeStart(cfg config) actionResult {
	if pid, err := guardianPID(cfg); err == nil && processAlive(pid) {
		if guardianProcessMatches(cfg, pid) {
			return actionResult{OK: true, Summary: "runtime already running", Data: map[string]any{"pid": pid}}
		}
		return actionResult{OK: false, Summary: "existing guardian ownership proof failed", Data: map[string]any{"pid": pid}}
	}
	info, err := os.Lstat(cfg.RunHelper)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&0o111 == 0 || info.Mode()&os.ModeSymlink != 0 {
		return actionResult{OK: false, Summary: "ordax-run helper is unavailable or unsafe", Data: map[string]any{}}
	}
	logPath := filepath.Join(cfg.StateDir, "runtime-launch.log")
	log, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return actionResult{OK: false, Summary: "cannot open runtime launch log", Data: map[string]any{"error": err.Error()}}
	}
	command := exec.Command(cfg.RunHelper)
	command.Env = []string{"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME=/root", "GIT_TERMINAL_PROMPT=0", "ORDAX_WORKTREE=" + cfg.Worktree}
	command.Stdout = log
	command.Stderr = log
	command.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := command.Start(); err != nil {
		_ = log.Close()
		return actionResult{OK: false, Summary: "runtime start failed", Data: map[string]any{"error": err.Error()}}
	}
	_ = log.Close()
	pid := command.Process.Pid
	go func() { _ = command.Wait() }()
	time.Sleep(2 * time.Second)
	if guardian, err := guardianPID(cfg); err == nil && processAlive(guardian) && guardianProcessMatches(cfg, guardian) {
		return actionResult{OK: true, Summary: "runtime started", Data: map[string]any{"launcher_pid": pid, "guardian_pid": guardian}}
	}
	return actionResult{OK: false, Summary: "runtime start did not establish guardian", Data: map[string]any{"launcher_pid": pid, "log": readBoundedFile(logPath, 16*1024)}}
}
