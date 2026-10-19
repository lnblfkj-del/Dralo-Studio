"""Add video accounts, presets and execution snapshots without changing existing data."""
from alembic import op

revision = "ds_r3_0002"
down_revision = "ds_r3_0001"
branch_labels = None
depends_on = None

STATEMENTS = [
  "ALTER TABLE provider_models ADD COLUMN video_config JSON",
  "\nCREATE TABLE video_adapter_releases (\n\tadapter_key VARCHAR(80) NOT NULL, \n\tversion VARCHAR(40) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tpackage JSON NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tid INTEGER NOT NULL, \n\tcreated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, \n\tupdated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, \n\tworkspace_id VARCHAR(32), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_video_adapter_release UNIQUE (workspace_id, adapter_key, version), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE RESTRICT\n)\n\n",
  "\nCREATE TABLE video_configuration_presets (\n\trelease_key VARCHAR(64) NOT NULL, \n\tname VARCHAR(80) NOT NULL, \n\tversion VARCHAR(40) NOT NULL, \n\tsource VARCHAR(200) NOT NULL, \n\tmodel_ids JSON NOT NULL, \n\tpackage JSON NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tid INTEGER NOT NULL, \n\tcreated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, \n\tupdated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, \n\tworkspace_id VARCHAR(32), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_video_preset_release UNIQUE (workspace_id, release_key), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE RESTRICT\n)\n\n",
  "\nCREATE TABLE video_accounts (\n\tprovider_id INTEGER NOT NULL, \n\tcurrent BOOLEAN NOT NULL, \n\tkey_ciphertext TEXT NOT NULL, \n\tkey_hint VARCHAR(8) NOT NULL, \n\tid INTEGER NOT NULL, \n\tcreated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, \n\tupdated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, \n\tworkspace_id VARCHAR(32), \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(provider_id) REFERENCES providers (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE RESTRICT\n)\n\n",
  "\nCREATE TABLE video_execution_versions (\n\tmodel_id INTEGER NOT NULL, \n\taccount_id INTEGER NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tsnapshot JSON NOT NULL, \n\tid INTEGER NOT NULL, \n\tcreated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, \n\tupdated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, \n\tworkspace_id VARCHAR(32), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_video_execution_version UNIQUE (model_id, fingerprint), \n\tFOREIGN KEY(model_id) REFERENCES provider_models (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(account_id) REFERENCES video_accounts (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE RESTRICT\n)\n\n",
  "CREATE INDEX ix_video_adapter_releases_workspace_id ON video_adapter_releases (workspace_id)",
  "CREATE UNIQUE INDEX uq_video_adapter_local_release ON video_adapter_releases (adapter_key, version) WHERE workspace_id IS NULL",
  "CREATE INDEX ix_video_configuration_presets_workspace_id ON video_configuration_presets (workspace_id)",
  "CREATE UNIQUE INDEX uq_video_configuration_presets_local_release_key ON video_configuration_presets (release_key) WHERE workspace_id IS NULL",
  "CREATE INDEX ix_video_accounts_provider_id ON video_accounts (provider_id)",
  "CREATE INDEX ix_video_accounts_workspace_id ON video_accounts (workspace_id)",
  "CREATE UNIQUE INDEX uq_video_account_current ON video_accounts (provider_id) WHERE \"current\" = true",
  "CREATE INDEX ix_video_execution_versions_account_id ON video_execution_versions (account_id)",
  "CREATE INDEX ix_video_execution_versions_model_id ON video_execution_versions (model_id)",
  "CREATE INDEX ix_video_execution_versions_workspace_id ON video_execution_versions (workspace_id)"
]

def upgrade():
    if op.get_bind().dialect.name != "sqlite":
        raise RuntimeError("Standalone migrations support SQLite only")
    for statement in STATEMENTS:
        op.execute(statement)

def downgrade():
    raise RuntimeError("Destructive standalone downgrade is not supported")
