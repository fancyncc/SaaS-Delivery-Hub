"""Native pgvector storage with JSON compatibility for offline SQLite tests."""
import json

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator, UserDefinedType


class PGVector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions=1024):
        self.dimensions = dimensions

    def get_col_spec(self, **kw):
        return f"VECTOR({self.dimensions})"


class Vector1024(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(PGVector() if dialect.name == "postgresql" else JSON())

    def process_bind_param(self, value, dialect):
        if value is not None and len(value) != 1024:
            raise ValueError("knowledge vector dimension must be 1024; migrate before changing dimensions")
        return json.dumps(value) if value is not None and dialect.name == "postgresql" else value

    def process_result_value(self, value, dialect):
        return json.loads(value) if isinstance(value, str) else value


class Vector512(Vector1024):
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(PGVector(512) if dialect.name == "postgresql" else JSON())

    def process_bind_param(self, value, dialect):
        if value is not None and len(value) != 512:
            raise ValueError("knowledge vector dimension must be 512; rebuild the BGE index")
        return json.dumps(value) if value is not None and dialect.name == "postgresql" else value


class PGAnyVector(PGVector):
    def get_col_spec(self, **kw):
        return 'VECTOR'


class V3Vector(Vector1024):
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(PGAnyVector() if dialect.name == 'postgresql' else JSON())

    def process_bind_param(self, value, dialect):
        if value is not None and len(value) not in (512,1024):
            raise ValueError('V3 model profiles support 512 or 1024 dimensions')
        return json.dumps(value) if value is not None and dialect.name == 'postgresql' else value
