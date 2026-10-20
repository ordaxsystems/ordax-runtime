package main

import (
  "testing"
  "time"
)

func TestCanonicalOwnerRetryDelay(t *testing.T) {
  cases := []struct{
    consecutive int
    expected time.Duration
  }{
    {1,5*time.Second},
    {2,10*time.Second},
    {3,20*time.Second},
    {4,30*time.Second},
    {9,30*time.Second},
  }
  for _,tc := range cases {
    if actual:=canonicalOwnerRetryDelay(tc.consecutive);actual!=tc.expected {
      t.Fatalf("retry %d: got %s want %s",tc.consecutive,actual,tc.expected)
    }
  }
}

func TestCanonicalOwnerIdleCadenceQueryBudget(t *testing.T) {
  // The canonical owner loop includes one heartbeat and one empty claim per
  // cycle. Keep the baseline well below the Cloudflare Free daily allowance.
  if canonicalOwnerIdlePollInterval < 10*time.Second {
    t.Fatal("canonical owner idle claim cadence is too aggressive")
  }
  iterations := int((24*time.Hour)/canonicalOwnerIdlePollInterval)
  const maxRoundTripsPerCycle = 4
  if iterations * maxRoundTripsPerCycle >= 40000 {
    t.Fatal("idle canonical owner loop consumes too many daily SQL queries")
  }
}
