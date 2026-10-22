"""No network, no filesystem: reject malformed/foreign Product proof projections."""
from __future__ import annotations

import copy
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import replace

from ordax_dev_agent.project_workspace_live_proof import project_workspace_live_proof
from ordax_dev_agent.project_workspace_port import WorkspaceActor
from ordax_dev_agent.workspace_resource_selection import (
    OwnerCanonicalProjectScope, LiveWorkspaceAuthority,
)

def _uuid():
    return str(uuid.uuid4())


class ProductWorkspaceLiveProofTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 22, 9, 15, tzinfo=timezone.utc)
        self.owner, self.space, self.project, self.client = (_uuid() for _ in range(4))
        self.actor_id, self.device, self.grant = (_uuid() for _ in range(3))
        self.actor = WorkspaceActor(self.actor_id,self.client,"workspace_a","lab_a")
        self.resource = "resource_" + uuid.uuid4().hex
        self.scope = OwnerCanonicalProjectScope(
            self.owner,self.client,self.space,self.project,self.device,
            "ordaxsystems/ordax-picker-fixture",
        )
        self.reply = {"ok":True,"schema":"ordax.project-workspace-resource/1","grant":{
            "grant_id":self.grant,"owner_user_id":self.owner,
            "space_id":self.space,"project_id":self.project,
            "lab_project_id":self.actor.project_id,"actor_id":self.actor.actor_id,
            "client_id":self.client,"workspace_session_id":self.actor.workspace_session_id,
            "device_id":self.device,"resource_id":self.resource,
            "allowed_actions":["read"],"issued_by":"trusted-runtime-picker",
            "issued_at":(self.now-timedelta(minutes=5)).isoformat(),
            "valid_until":(self.now+timedelta(minutes=55)).isoformat(),
            "revoked_at":None,"active":True,
        }}

    def project(self, response=None, **kwargs):
        return project_workspace_live_proof(
            self.reply if response is None else response,
            scope=kwargs.get("scope",self.scope),
            actor=kwargs.get("actor",self.actor),
            device_id=kwargs.get("device_id",self.device),
            resource_id=kwargs.get("resource_id",self.resource),
            now=kwargs.get("now",self.now),
        )

    def test_sql_projection_yields_only_exact_scoped_read_authority(self):
        proof=self.project()
        self.assertIsInstance(proof,LiveWorkspaceAuthority)
        self.assertEqual(proof.owner_user_id,self.owner)
        self.assertEqual(proof.canonical_project_id,self.project)
        self.assertEqual(proof.resource_id,self.resource)
        self.assertEqual(proof.grant_id,self.grant)
        self.assertEqual(proof.allowed_actions,frozenset({"read"}))
        self.assertEqual(proof.issued_at,self.now-timedelta(minutes=5))
        self.assertEqual(proof.expires_at,self.now+timedelta(minutes=55))
        self.assertNotIn("root",repr(proof))

    def test_every_identity_dimension_is_bound_to_trusted_expected_values(self):
        for key in (
            "owner_user_id","space_id","project_id","lab_project_id",
            "actor_id","client_id","workspace_session_id","device_id","resource_id",
        ):
            sample=copy.deepcopy(self.reply)
            sample["grant"][key]=(
                "another_lab" if key=="lab_project_id"
                else "another_workspace" if key=="workspace_session_id"
                else "resource_"+uuid.uuid4().hex if key=="resource_id"
                else _uuid()
            )
            with self.subTest(field=key):
                self.assertIsNone(self.project(sample))
        self.assertIsNone(self.project(scope=replace(self.scope,owner_user_id=_uuid())))
        self.assertIsNone(self.project(scope=replace(self.scope,space_id=_uuid())))
        self.assertIsNone(self.project(scope=replace(self.scope,project_id=_uuid())))
        self.assertIsNone(self.project(scope=replace(self.scope,client_id=_uuid())))
        self.assertIsNone(self.project(scope=replace(self.scope,device_id=_uuid())))
        self.assertIsNone(self.project(actor=replace(self.actor,actor_id=_uuid())))
        self.assertIsNone(self.project(actor=replace(self.actor,project_id="other_project")))
        self.assertIsNone(self.project(actor=replace(self.actor,client_id=_uuid())))
        self.assertIsNone(self.project(device_id=_uuid()))
        self.assertIsNone(self.project(resource_id="resource_"+uuid.uuid4().hex))

    def test_revoked_inactive_wrong_profile_write_or_untrusted_picker_denies(self):
        for field,value in (
            ("revoked_at",self.now.isoformat()),("active",False),
            ("active",1),("issued_by","model"),("issued_by","legacy"),
            ("allowed_actions",["read","write"]),("allowed_actions",["write"]),
            ("allowed_actions",[]),("allowed_actions","read"),
            ("grant_id","untrusted-grant"),
        ):
            sample=copy.deepcopy(self.reply)
            sample["grant"][field]=value
            with self.subTest(field=field,value=value):
                self.assertIsNone(self.project(sample))

    def test_missing_extra_and_non_object_fields_fail_closed(self):
        for reply in (
            None,{},[],{"ok":True,"schema":"ordax.project-workspace-resource/1"},
            {"ok":False,"schema":"ordax.project-workspace-resource/1","grant":self.reply["grant"]},
            {"ok":1,"schema":"ordax.project-workspace-resource/1","grant":self.reply["grant"]},
            {"ok":True,"schema":"wrong","grant":self.reply["grant"]},
            dict(self.reply, extra="token"),
            dict(self.reply, grant=[self.reply["grant"]]),
            dict(self.reply, grant=None),
        ):
            with self.subTest(reply=str(reply)[:60]):
                self.assertIsNone(self.project(reply))
        for key in self.reply["grant"]:
            sample=copy.deepcopy(self.reply)
            del sample["grant"][key]
            with self.subTest(missing=key):
                self.assertIsNone(self.project(sample))
        sample=copy.deepcopy(self.reply)
        sample["grant"]["path"]="C:\\Users\\Private"
        self.assertIsNone(self.project(sample))

    def test_original_24h_lifetime_and_precise_boundaries(self):
        samples=(
            ("issued_at",(self.now+timedelta(seconds=1)).isoformat()),
            ("issued_at",(self.now-timedelta(days=3)).isoformat()),
            ("issued_at","yesterday"),
            ("issued_at",None),
            ("issued_at","2026-10-22T10:00:00"),
            ("valid_until",self.now.isoformat()),
            ("valid_until",(self.now-timedelta(seconds=1)).isoformat()),
            ("valid_until",(self.now+timedelta(days=3)).isoformat()),
            ("valid_until",True),
            ("valid_until","9999-99-99T25:25:25+00:00"),
            ("valid_until","2026-10-22T10:00:00"),
        )
        for key,value in samples:
            sample=copy.deepcopy(self.reply)
            sample["grant"][key]=value
            with self.subTest(field=key,value=value):
                self.assertIsNone(self.project(sample))
        future=copy.deepcopy(self.reply)
        future["grant"]["issued_at"]=(self.now-timedelta(hours=23)).isoformat()
        future["grant"]["valid_until"]=(self.now+timedelta(hours=2)).isoformat()
        self.assertIsNone(self.project(future))  # original lifetime 25h
        accepted=copy.deepcopy(self.reply)
        accepted["grant"]["issued_at"]=(self.now-timedelta(hours=23)).isoformat()
        accepted["grant"]["valid_until"]=(self.now+timedelta(hours=1)).isoformat()
        self.assertIsNotNone(self.project(accepted))

    def test_noncanonical_actor_and_resource_fail_before_projection(self):
        self.assertIsNone(self.project(actor=replace(self.actor,project_id="../project")))
        self.assertIsNone(self.project(actor=replace(self.actor,workspace_session_id="untrusted id")))
        self.assertIsNone(self.project(resource_id="../private"))
        self.assertIsNone(self.project(scope=replace(self.scope,owner_user_id="owner")))
        self.assertIsNone(self.project(now=datetime(2026,10,22,9,15)))


if __name__=="__main__":
    unittest.main()
