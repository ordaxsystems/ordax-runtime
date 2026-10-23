package main

import (
	"errors"
	"log"
	"os"
	"time"
)

func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)
	if err := run(); err != nil {
		log.Printf("ordax-owner-recovery: %v", err)
		os.Exit(1)
	}
}

// Old per-device transport overrides are rejected instead of silently
// selecting deprecated or alternate authorities.
func rejectObsoleteTransportOverride() error {
	if os.Getenv("ORDAX_OWNER_TRANSPORT") != "" {
		return errors.New("ORDAX_OWNER_TRANSPORT is obsolete: canonical PostgreSQL transport is mandatory")
	}
	return nil
}

func run() error {
	if err := rejectObsoleteTransportOverride(); err != nil {
		return err
	}
	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(cfg.StateDir, 0o700); err != nil {
		return err
	}
	return runCanonicalOwnerRecovery(cfg)
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
