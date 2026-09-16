"""Explicit backfill of authorized database sources; never scans filesystem inputs."""
import argparse
import asyncio

from sqlalchemy import text

from backend.db import SessionLocal
from backend.models import Tenant
from backend.retrieval_sources import backfill


async def run(tenant_id):
    async with SessionLocal() as session:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None or tenant.status != 'active':
            raise ValueError('active tenant not found')
        if session.bind.dialect.name == 'postgresql':
            await session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
                                  {'tenant': tenant_id})
            await session.execute(text("SELECT set_config('app.current_company_role', 'company_admin', true)"))
        count = await backfill(session, tenant_id)
        await session.commit()
        print({'sources_registered': count})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tenant-id', required=True)
    args = parser.parse_args()
    asyncio.run(run(args.tenant_id))
