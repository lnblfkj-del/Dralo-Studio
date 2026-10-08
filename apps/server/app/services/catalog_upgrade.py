"""Offline catalog upgrade; service stop and matched backup are prerequisites."""

import argparse
import asyncio
import json

from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.core.workspace_context import system_scope, workspace_scope
from app.models.workspace import Workspace
from app.services.reviewed_catalog_service import catalog, install_skills, install_styles


async def run(apply=False, *, keys: set[str] | None = None):
    if keys is not None and keys - {entry["definition"]["key"] for entry in catalog()["skills"]}:
        raise ValueError("Unknown catalog skills")
    report = {"release": catalog()["release"], "applied": apply, "workspaces": [],
              "skills": sorted(keys) if keys is not None else "all", "styles_upgraded": keys is None}
    try:
        with system_scope():
            async with SessionLocal() as discovery:
                workspaces = list(await discovery.scalars(select(Workspace).where(Workspace.personal_user_id.is_not(None))))
        # Single transaction for all workspaces, with independent scoped sessions.
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                # sqlite3 legacy transaction mode does not BEGIN before SAVEPOINT.
                if connection.dialect.name == "sqlite":
                    await connection.exec_driver_sql("BEGIN")
                with system_scope():
                    async with SessionLocal(bind=connection, join_transaction_mode="create_savepoint") as session:
                        await install_skills(session, upgrade=True, keys=keys)
                        await session.commit()
                for workspace in workspaces:
                    with workspace_scope(workspace.id, workspace.personal_user_id, "owner"):
                        async with SessionLocal(bind=connection, join_transaction_mode="create_savepoint") as session:
                            await install_skills(session, upgrade=True, keys=keys)
                            if keys is None:
                                await install_styles(session, upgrade=True)
                            await session.commit()
                            report["workspaces"].append(workspace.id)
                if apply:
                    await transaction.commit()
                else:
                    await transaction.rollback()
            except BaseException:
                await transaction.rollback()
                raise
        print(json.dumps(report))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--install-media", action="store_true")
    parser.add_argument("--skills", nargs="+", help="Upgrade only these Skill keys; do not install styles")
    args = parser.parse_args()
    if args.skills and args.install_media:
        parser.error("A targeted Skill upgrade cannot install style media")
    if args.install_media:
        from app.core.config import settings
        from app.services.builtin_style_media_service import install_files
        install_files(settings.storage_path)
    asyncio.run(run(args.apply, keys=set(args.skills) if args.skills is not None else None))
