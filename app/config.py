import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./certificates.db"))
    certificates_dir: str = field(default_factory=lambda: os.getenv("CERTIFICATES_DIR", "./generated_certificates"))
    max_recipients_per_job: int = field(default_factory=lambda: int(os.getenv("MAX_RECIPIENTS_PER_JOB", "1000")))
