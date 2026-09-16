"""Independent derived index; never consulted by chat or Agent retrieval."""
from sqlalchemy import ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.models import Base, uid
from backend.vector_type import V3Vector


class V3Document(Base):
    __tablename__='rag_v3_documents'
    __table_args__=(UniqueConstraint('tenant_id','origin_id'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey('customer_tenants.id'),index=True)
    origin_id: Mapped[str]=mapped_column(ForeignKey('knowledge_documents.id'),index=True)
    filename: Mapped[str]=mapped_column(String(200))
    raw: Mapped[bytes]=mapped_column(LargeBinary)
    options: Mapped[dict]=mapped_column(JSON,default=dict)
    phase: Mapped[str]=mapped_column(String(24),default='pending',index=True)
    error: Mapped[str]=mapped_column(String(300),default='')
    warnings: Mapped[list]=mapped_column(JSON,default=list)
    digest: Mapped[str]=mapped_column(String(64))
    identity: Mapped[str]=mapped_column(String(200),default='')
    attempts: Mapped[int]=mapped_column(Integer,default=0)
    metrics: Mapped[dict]=mapped_column(JSON,default=dict)


class V3Node(Base):
    __tablename__='rag_v3_nodes'
    id: Mapped[str]=mapped_column(String(80),primary_key=True)
    tenant_id: Mapped[str]=mapped_column(ForeignKey('customer_tenants.id'),index=True)
    document_id: Mapped[str]=mapped_column(ForeignKey('rag_v3_documents.id',ondelete='CASCADE'),index=True)
    structure: Mapped[dict]=mapped_column(JSON)


class V3Unit(Base):
    __tablename__='rag_v3_units'
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    tenant_id: Mapped[str]=mapped_column(ForeignKey('customer_tenants.id'),index=True)
    document_id: Mapped[str]=mapped_column(ForeignKey('rag_v3_documents.id',ondelete='CASCADE'),index=True)
    node_id: Mapped[str]=mapped_column(String(80))
    ordinal: Mapped[int]=mapped_column(Integer)
    previous_id: Mapped[str | None]=mapped_column(String(100),nullable=True)
    next_id: Mapped[str | None]=mapped_column(String(100),nullable=True)
    text: Mapped[str]=mapped_column(Text)
    heading: Mapped[str]=mapped_column(Text,default='')
    kind: Mapped[str]=mapped_column(String(30))
    location: Mapped[dict]=mapped_column(JSON)
    parent: Mapped[str | None]=mapped_column(String(80),nullable=True)
    record: Mapped[str | None]=mapped_column(String(200),nullable=True)
    tokens: Mapped[int]=mapped_column(Integer)
    lexemes: Mapped[str]=mapped_column(Text)
    identifiers: Mapped[list]=mapped_column(JSON,default=list)
    embedding: Mapped[list | None]=mapped_column(V3Vector(),nullable=True)
