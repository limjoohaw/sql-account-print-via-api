"""Company configuration management — load/save companies.json."""

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from typing import Optional

from config import settings
from crypto import encrypt_value, decrypt_value

_companies_lock = threading.Lock()


@dataclass
class Company:
    id: str
    name: str
    api_host: str
    access_key: str
    secret_key: str
    templates: dict = field(default_factory=dict)
    # templates: {doc_type_key: [{"name": str, "engine": str, "built_in": bool}, ...]}


def load_companies() -> list[Company]:
    """Load all companies from companies.json. Decrypts API keys in memory."""
    path = settings.companies_path
    with _companies_lock:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    companies = []
    for c in data:
        c["access_key"] = decrypt_value(c.get("access_key", ""))
        c["secret_key"] = decrypt_value(c.get("secret_key", ""))
        companies.append(Company(**c))
    return companies


def save_companies(companies: list[Company]):
    """Save all companies to companies.json. Encrypts API keys at rest."""
    path = settings.companies_path
    data = []
    for c in companies:
        d = asdict(c)
        d["access_key"] = encrypt_value(d["access_key"])
        d["secret_key"] = encrypt_value(d["secret_key"])
        data.append(d)
    with _companies_lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def find_company(company_id: str) -> Optional[Company]:
    """Find a company by ID."""
    for c in load_companies():
        if c.id == company_id:
            return c
    return None


def add_company(company: Company):
    """Add a new company."""
    companies = load_companies()
    for c in companies:
        if c.id == company.id:
            raise ValueError(f"Company ID '{company.id}' already exists")
    companies.append(company)
    save_companies(companies)


def update_company(company_id: str, **kwargs) -> Optional[Company]:
    """Update a company's fields and save."""
    companies = load_companies()
    for c in companies:
        if c.id == company_id:
            for key, value in kwargs.items():
                if hasattr(c, key):
                    setattr(c, key, value)
            save_companies(companies)
            return c
    return None


def delete_company(company_id: str) -> bool:
    """Delete a company by ID."""
    companies = load_companies()
    original_len = len(companies)
    companies = [c for c in companies if c.id != company_id]
    if len(companies) < original_len:
        save_companies(companies)
        return True
    return False


def get_companies_for_user(company_ids: list[str]) -> list[Company]:
    """Get companies accessible by a user (filtered by their company ID list)."""
    all_companies = load_companies()
    if not company_ids:
        return []
    return [c for c in all_companies if c.id in company_ids]
