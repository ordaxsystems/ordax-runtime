package main

import (
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)
	if err := run(); err != nil {
		log.Printf("ordax-owner-recovery: %v", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(cfg.StateDir, 0o700); err != nil {
		return err
	}
	client, err := newClient(cfg)
	if err != nil {
		return err
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(stop)

	backoff := time.Second
	for {
		select {
		case <-stop:
			client.close()
			return nil
		default:
		}
		// A completed local mutation must have its exact terminal receipt
		// accepted before this process is allowed to claim another job.
		if err := client.recoverPendingReports(); err != nil {
			log.Printf("pending terminal report recovery unavailable: %v", err)
			if !sleepOrStop(stop, backoff) {
				return nil
			}
			if backoff < 30*time.Second {
				backoff *= 2
			}
			continue
		}
		if err := client.connect(); err != nil {
			log.Printf("connection unavailable: %v", err)
			if !sleepOrStop(stop, backoff) {
				return nil
			}
			if backoff < 30*time.Second {
				backoff *= 2
			}
			continue
		}
		backoff = time.Second
		if err := client.session(stop); err != nil {
			client.close()
			log.Printf("session ended: %v", err)
			if !sleepOrStop(stop, backoff) {
				return nil
			}
			continue
		}
		client.close()
	}
}

func sleepOrStop(stop <-chan os.Signal, duration time.Duration) bool {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-stop:
		return false
	case <-timer.C:
		return true
	}
}

func (c *client) session(stop <-chan os.Signal) error {
	heartbeat := time.NewTicker(25 * time.Second)
	defer heartbeat.Stop()
	for {
		select {
		case <-stop:
			return nil
		case err := <-c.readErr:
			return err
		case <-heartbeat.C:
			if err := c.heartbeat(); err != nil {
				return err
			}
		case <-c.serverPing:
			if err := c.heartbeat(); err != nil {
				return err
			}
		case message := <-c.jobs:
			job, _, err := decodeJob(message, c.agentInstanceID, c.bootID)
			if err != nil {
				log.Printf("rejected job envelope: %v", err)
				continue
			}
			if err := c.rpc("start", contextMap(job), 15*time.Second); err != nil {
				return err
			}
			result, err := c.executeWithLease(job)
			if err != nil {
				return err
			}
			if err := c.persistAndReport(job, result); err != nil {
				return err
			}
		}
	}
}

func (c *client) heartbeat() error {
	return c.rpc("heartbeat", map[string]any{
		"boot_id":           c.bootID,
		"agent_instance_id": c.agentInstanceID,
		"runtime_mode":      "owner-development",
		"status":            "online",
		"agent_version":     version,
		"capabilities":      allowedCapabilities(),
		"metadata":          map[string]any{"platform": "linux", "profile": "owner-development"},
	}, 10*time.Second)
}

func (c *client) executeWithLease(job jobEnvelope) (actionResult, error) {
	resultCh := make(chan actionResult, 1)
	go func() {
		resultCh <- executeCapability(c.cfg, job.Capability, job.EffectID)
	}()
	renew := time.NewTicker(30 * time.Second)
	defer renew.Stop()
	for {
		select {
		case result := <-resultCh:
			return result, nil
		case <-renew.C:
			if err := c.rpc("lease_heartbeat", contextMap(job), 15*time.Second); err != nil {
				return actionResult{}, err
			}
		}
	}
}
