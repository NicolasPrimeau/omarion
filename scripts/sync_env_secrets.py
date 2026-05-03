#!/usr/bin/env python3
"""
Sync .env with AWS Secrets Manager (artel/env).

Usage:
    uv run python scripts/sync_env_secrets.py            # bidirectional sync
    uv run python scripts/sync_env_secrets.py --pull     # overwrite local from remote
    uv run python scripts/sync_env_secrets.py --strategy local   # prefer local on conflict
"""

import json
import sys
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import boto3
from dotenv import dotenv_values
from pydantic import BaseModel, Field


class ConflictStrategy(StrEnum):
    ABORT = "abort"
    LOCAL = "local"
    REMOTE = "remote"
    MANUAL = "manual"


class SecretMetadata(BaseModel):
    timestamp: str
    hash: str

    @classmethod
    def create(cls, value: str) -> "SecretMetadata":
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            hash=cls._hash(value),
        )

    @staticmethod
    def _hash(value: str) -> str:
        import hashlib
        return hashlib.sha256(value.encode()).hexdigest()


class SecretsStore(BaseModel):
    secrets: dict[str, SecretMetadata] = Field(default_factory=dict)
    last_sync: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ConflictInfo(BaseModel):
    key: str
    local_value: str
    remote_value: str


class SyncResult(BaseModel):
    added: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    conflicts: list[ConflictInfo] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)


class EnvSecretsSync:
    def __init__(self, secret_name: str, env_file: Path = Path(".env")):
        self.secret_name = secret_name
        self.env_file = env_file
        self.meta_secret_name = f"{secret_name}/meta"
        self.client = boto3.client("secretsmanager")
        self.local_meta_file = env_file.parent / ".env.meta.json"

    def _load_local_env(self) -> dict[str, str]:
        if not self.env_file.exists():
            raise FileNotFoundError(f"{self.env_file} not found")
        return dict(dotenv_values(self.env_file))

    def _load_local_store(self) -> SecretsStore:
        if not self.local_meta_file.exists():
            return SecretsStore()
        return SecretsStore.model_validate_json(self.local_meta_file.read_text())

    def _save_local_store(self, store: SecretsStore) -> None:
        self.local_meta_file.write_text(store.model_dump_json(indent=2))

    def _get_remote_values(self) -> dict[str, str]:
        try:
            response = self.client.get_secret_value(SecretId=self.secret_name)
            data = json.loads(response["SecretString"])
            if "secrets" in data:
                return {k: v["value"] for k, v in data["secrets"].items() if "value" in v}
            return {k: v for k, v in data.items() if isinstance(v, str)}
        except self.client.exceptions.ResourceNotFoundException:
            return {}

    def _put_remote_values(self, values: dict[str, str]) -> None:
        secret_string = json.dumps(values)
        try:
            self.client.update_secret(SecretId=self.secret_name, SecretString=secret_string)
        except self.client.exceptions.ResourceNotFoundException:
            self.client.create_secret(Name=self.secret_name, SecretString=secret_string)

    def _get_remote_store(self) -> SecretsStore:
        try:
            response = self.client.get_secret_value(SecretId=self.meta_secret_name)
            return SecretsStore.model_validate_json(response["SecretString"])
        except self.client.exceptions.ResourceNotFoundException:
            return SecretsStore()

    def _put_remote_store(self, store: SecretsStore) -> None:
        store.last_sync = datetime.now(timezone.utc).isoformat()
        secret_string = store.model_dump_json(indent=2)
        try:
            self.client.update_secret(SecretId=self.meta_secret_name, SecretString=secret_string)
        except self.client.exceptions.ResourceNotFoundException:
            self.client.create_secret(Name=self.meta_secret_name, SecretString=secret_string)

    def sync(self, strategy: ConflictStrategy = ConflictStrategy.ABORT) -> SyncResult:
        local_values = self._load_local_env()
        local_store = self._load_local_store()
        remote_values = self._get_remote_values()
        remote_store = self._get_remote_store()
        result = SyncResult()

        new_values: dict[str, str] = {}
        new_store = SecretsStore()

        all_keys = set(local_values) | set(remote_values)

        for key in all_keys:
            local_val = local_values.get(key)
            remote_val = remote_values.get(key)
            last_meta = local_store.secrets.get(key)
            last_hash = last_meta.hash if last_meta else None

            local_hash = SecretMetadata._hash(local_val) if local_val else None
            remote_hash = SecretMetadata._hash(remote_val) if remote_val else None

            local_changed = local_hash != last_hash
            remote_changed = remote_hash != last_hash

            if not remote_val:
                if local_changed:
                    new_values[key] = local_val
                    new_store.secrets[key] = SecretMetadata.create(local_val)
                    result.added.append(key)
                else:
                    result.updated.append(f"{key} (deleted remotely)")
            elif not local_val:
                if remote_changed:
                    new_values[key] = remote_val
                    new_store.secrets[key] = remote_store.secrets.get(key, SecretMetadata.create(remote_val))
                    result.added.append(key)
                else:
                    result.updated.append(f"{key} (deleted locally)")
            elif local_hash == remote_hash:
                new_values[key] = local_val
                new_store.secrets[key] = SecretMetadata.create(local_val)
                result.unchanged.append(key)
            elif local_changed and not remote_changed:
                new_values[key] = local_val
                new_store.secrets[key] = SecretMetadata.create(local_val)
                result.updated.append(key)
            elif remote_changed and not local_changed:
                new_values[key] = remote_val
                new_store.secrets[key] = remote_store.secrets.get(key, SecretMetadata.create(remote_val))
                result.updated.append(key)
            else:
                conflict = ConflictInfo(key=key, local_value=local_val, remote_value=remote_val)
                result.conflicts.append(conflict)

                if strategy == ConflictStrategy.ABORT:
                    new_values[key] = local_val
                    new_store.secrets[key] = SecretMetadata.create(local_val)
                elif strategy == ConflictStrategy.LOCAL:
                    new_values[key] = local_val
                    new_store.secrets[key] = SecretMetadata.create(local_val)
                elif strategy == ConflictStrategy.REMOTE:
                    new_values[key] = remote_val
                    new_store.secrets[key] = remote_store.secrets.get(key, SecretMetadata.create(remote_val))
                else:
                    resolved = self._manual_resolve(conflict)
                    new_values[key] = resolved
                    new_store.secrets[key] = SecretMetadata.create(resolved)

        if result.conflicts and strategy == ConflictStrategy.ABORT:
            return result

        env_lines = [f"{key}={value}" for key, value in sorted(new_values.items())]
        self.env_file.write_text("\n".join(env_lines) + "\n")
        self._put_remote_values(new_values)
        self._put_remote_store(new_store)
        self._save_local_store(new_store)
        return result

    def _manual_resolve(self, conflict: ConflictInfo) -> str:
        print(f"\nconflict: {conflict.key}")
        print(f"  local:  {conflict.local_value}")
        print(f"  remote: {conflict.remote_value}")
        print("\n  [1] use local  [2] use remote  [3] enter value")
        while True:
            choice = input("choice: ").strip()
            if choice == "1":
                return conflict.local_value
            elif choice == "2":
                return conflict.remote_value
            elif choice == "3":
                return input("value: ").strip()

    def pull(self) -> None:
        remote_values = self._get_remote_values()
        if not remote_values:
            print("no secrets found in remote store")
            return
        env_lines = [f"{key}={value}" for key, value in sorted(remote_values.items())]
        self.env_file.write_text("\n".join(env_lines) + "\n")
        new_store = SecretsStore(secrets={k: SecretMetadata.create(v) for k, v in remote_values.items()})
        self._save_local_store(new_store)
        print(f"pulled {len(remote_values)} secrets to {self.env_file}")


def _print_result(result: SyncResult) -> None:
    print()
    if result.added:
        print(f"added     ({len(result.added)}): {', '.join(result.added)}")
    if result.updated:
        print(f"updated   ({len(result.updated)}): {', '.join(result.updated)}")
    if result.unchanged:
        print(f"unchanged ({len(result.unchanged)}): {', '.join(result.unchanged)}")
    if result.conflicts:
        print(f"conflicts ({len(result.conflicts)}):")
        for c in result.conflicts:
            print(f"  ! {c.key}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Sync .env with AWS Secrets Manager")
    parser.add_argument("--secret-name", default="artel/env")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--strategy", type=ConflictStrategy, default=ConflictStrategy.ABORT, choices=list(ConflictStrategy))
    parser.add_argument("--pull", action="store_true", help="overwrite local from remote")
    args = parser.parse_args()

    try:
        syncer = EnvSecretsSync(secret_name=args.secret_name, env_file=args.env_file)
        if args.pull:
            syncer.pull()
        else:
            result = syncer.sync(strategy=args.strategy)
            _print_result(result)
            if result.conflicts and args.strategy == ConflictStrategy.ABORT:
                print("\naborted due to conflicts — use --strategy to resolve")
                sys.exit(1)
            else:
                print("\nsync complete")
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
