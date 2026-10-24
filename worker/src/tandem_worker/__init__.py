"""Tandem's planning worker. Human consent is deliberately outside this package."""
import os

# Private owner contexts must not be exported by inherited LangSmith settings.
# Observability is through the allowlisted local event/report surface in v1.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
