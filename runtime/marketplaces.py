"""Amazon marketplaces, the selling region each belongs to, and its SP-API host.

The table is closed. A connection names one marketplace by its country code; the
region and the API host are derived from that choice here and never accepted
from workflow input, so a workflow cannot point a connection at another region.

Source: Amazon "Marketplace IDs" (updated 2026-10-23) and "SP-API endpoints"
(updated 2026-09-09). Amazon uses ``UK`` rather than ``GB`` for the United
Kingdom, and so does this table.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import errors
from .errors import ExtensionError

REGION_NA = "NA"
REGION_EU = "EU"
REGION_FE = "FE"

#: The production SP-API host of each selling region.
REGION_HOSTS: dict[str, str] = {
    REGION_NA: "sellingpartnerapi-na.amazon.com",
    REGION_EU: "sellingpartnerapi-eu.amazon.com",
    REGION_FE: "sellingpartnerapi-fe.amazon.com",
}


@dataclass(frozen=True)
class Marketplace:
    """One Amazon store a seller can sell in."""

    code: str
    country: str
    marketplace_id: str
    region: str

    @property
    def api_host(self) -> str:
        return REGION_HOSTS[self.region]

    @property
    def api_base_url(self) -> str:
        return f"https://{self.api_host}"


MARKETPLACES: tuple[Marketplace, ...] = (
    Marketplace("CA", "Canada", "A2EUQ1WTGCTBG2", REGION_NA),
    Marketplace("US", "United States", "ATVPDKIKX0DER", REGION_NA),
    Marketplace("MX", "Mexico", "A1AM78C64UM0Y8", REGION_NA),
    Marketplace("BR", "Brazil", "A2Q3Y263D00KWC", REGION_NA),
    Marketplace("IE", "Ireland", "A28R8C7NBKEWEA", REGION_EU),
    Marketplace("ES", "Spain", "A1RKKUPIHCS9HS", REGION_EU),
    Marketplace("UK", "United Kingdom", "A1F83G8C2ARO7P", REGION_EU),
    Marketplace("FR", "France", "A13V1IB3VIYZZH", REGION_EU),
    Marketplace("BE", "Belgium", "AMEN7PMS3EDWL", REGION_EU),
    Marketplace("NL", "Netherlands", "A1805IZSGTT6HS", REGION_EU),
    Marketplace("DE", "Germany", "A1PA6795UKMFR9", REGION_EU),
    Marketplace("IT", "Italy", "APJ6JRA9NG5V4", REGION_EU),
    Marketplace("SE", "Sweden", "A2NODRKZP88ZB9", REGION_EU),
    Marketplace("ZA", "South Africa", "AE08WJ6YKNBMC", REGION_EU),
    Marketplace("PL", "Poland", "A1C3SOZRARQ6R3", REGION_EU),
    Marketplace("EG", "Egypt", "ARBP9OOSHTCHU", REGION_EU),
    Marketplace("TR", "Turkey", "A33AVAJ2PDY3EV", REGION_EU),
    Marketplace("SA", "Saudi Arabia", "A17E79C6D8DWNP", REGION_EU),
    Marketplace("AE", "United Arab Emirates", "A2VIGQ35RCS4UG", REGION_EU),
    Marketplace("IN", "India", "A21TJRUUN4KGV", REGION_EU),
    Marketplace("SG", "Singapore", "A19VAU5U5O7RUS", REGION_FE),
    Marketplace("AU", "Australia", "A39IBJ37TRP1C6", REGION_FE),
    Marketplace("JP", "Japan", "A1VC38T7YXB528", REGION_FE),
)

MARKETPLACES_BY_CODE: dict[str, Marketplace] = {item.code: item for item in MARKETPLACES}
MARKETPLACE_CODES: tuple[str, ...] = tuple(item.code for item in MARKETPLACES)
MARKETPLACE_IDS: frozenset[str] = frozenset(item.marketplace_id for item in MARKETPLACES)


def marketplace_for_code(value: object) -> Marketplace:
    """The marketplace a connection names, or a refusal that lists the choices."""
    code = value.strip().upper() if isinstance(value, str) else ""
    marketplace = MARKETPLACES_BY_CODE.get(code)
    if marketplace is None:
        raise ExtensionError(
            errors.INVALID_CONFIGURATION,
            "Choose the marketplace from the list of Amazon countries",
        )
    return marketplace


__all__ = [
    "MARKETPLACES",
    "MARKETPLACES_BY_CODE",
    "MARKETPLACE_CODES",
    "MARKETPLACE_IDS",
    "REGION_EU",
    "REGION_FE",
    "REGION_HOSTS",
    "REGION_NA",
    "Marketplace",
    "marketplace_for_code",
]
