package main

import (
 "errors"
)

// claimCanonicalOwnerJob validates the PostgreSQL claim envelope using the
// same payload-integrity and device execution fencing as the native agent.
// A nil job is a valid, empty queue and must never trigger local execution.
func claimCanonicalOwnerJob(transport canonicalDeviceTransport, agentInstanceID, bootID string) (*jobEnvelope,error) {
 ack,err:=transport.post("claim",map[string]any{
  "agent_instance_id":agentInstanceID,
  "boot_id":bootID,
  "runtime_kind":"ordax-os",
  "agent_version":version,
 })
 if err!=nil{return nil,err}
 raw,found:=ack["job"]
 if !found{return nil,errors.New("canonical claim missing job field")}
 if raw==nil{return nil,nil}
 job,_,err:=decodeJob(map[string]any{"job":raw},agentInstanceID,bootID)
 if err!=nil{return nil,err}
 return &job,nil
}
