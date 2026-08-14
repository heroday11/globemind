"""Checked-in source registrations backed by first-party API documentation."""

from __future__ import annotations

from .contracts import ConnectorDescriptor, LicenseEvidence, SourceEvidence, SourceId

REQUEST_TIMEOUT_SECONDS = 8.0


_DESCRIPTORS: dict[SourceId, ConnectorDescriptor] = {
    "world-bank": ConnectorDescriptor(
        source=SourceEvidence(
            source_id="world-bank",
            authority="World Bank",
            endpoint="https://api.worldbank.org/v2",
            documentation_url=(
                "https://datahelpdesk.worldbank.org/knowledgebase/articles/"
                "898581-api-basic-call-structures"
            ),
        ),
        api_version="Indicators API v2",
        license=LicenseEvidence(
            state="restricted",
            identifier="CC-BY-4.0-default-with-dataset-exceptions",
            terms_url="https://datacatalog.worldbank.org/public-licenses",
            scope=(
                "World Bank-produced open datasets default to CC BY 4.0, but the "
                "queried indicator can carry different dataset-level terms."
            ),
            caveats=[
                "Verify the individual indicator's dataset license before redistribution.",
                "Attribution and the World Bank additional dataset terms still apply.",
            ],
        ),
        maximum_records_per_request=100,
        cache_ttl_seconds=21600,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    ),
    "imf": ConnectorDescriptor(
        source=SourceEvidence(
            source_id="imf",
            authority="International Monetary Fund",
            endpoint="https://www.imf.org/external/datamapper/api/v2",
            documentation_url="https://www.imf.org/external/datamapper/api/",
        ),
        api_version="DataMapper API v2",
        license=LicenseEvidence(
            state="unknown",
            identifier=None,
            terms_url=None,
            scope=(
                "The official DataMapper API page documents access but does not state "
                "a reusable dataset license for arbitrary indicator responses."
            ),
            caveats=[
                "Production redistribution requires a separately verified IMF usage basis.",
                "Observed and projected periods are not distinguished by this adapter.",
            ],
        ),
        maximum_records_per_request=100,
        cache_ttl_seconds=21600,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    ),
    "un-sdg": ConnectorDescriptor(
        source=SourceEvidence(
            source_id="un-sdg",
            authority="United Nations Statistics Division",
            endpoint="https://unstats.un.org/SDGAPI/v1/sdg",
            documentation_url="https://unstats.un.org/SDGAPI/swagger/",
        ),
        api_version="UNSD SDG API v1",
        license=LicenseEvidence(
            state="unknown",
            identifier=None,
            terms_url=None,
            scope=(
                "The official Swagger contract defines the API and data fields, but no "
                "dataset-wide reuse license is asserted by this connector."
            ),
            caveats=[
                "Custodian-agency and series-specific terms require governance review.",
            ],
        ),
        maximum_records_per_request=50,
        cache_ttl_seconds=21600,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    ),
    "crossref": ConnectorDescriptor(
        source=SourceEvidence(
            source_id="crossref",
            authority="Crossref",
            endpoint="https://api.crossref.org/v1",
            documentation_url=(
                "https://www.crossref.org/documentation/retrieve-metadata/rest-api/"
            ),
        ),
        api_version="REST API v1",
        license=LicenseEvidence(
            state="restricted",
            identifier="Crossref-public-metadata-with-record-level-exceptions",
            terms_url=(
                "https://www.crossref.org/documentation/retrieve-metadata/rest-api/"
            ),
            scope=(
                "Crossref states that almost all metadata can be reused, while some "
                "publisher-supplied fields can remain copyrighted."
            ),
            caveats=[
                "Abstracts, references, and full-text links are deliberately excluded.",
                "Record-level publisher terms remain outside this adapter's evidence.",
            ],
        ),
        maximum_records_per_request=20,
        cache_ttl_seconds=900,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    ),
}


def connector_descriptor(source_id: SourceId) -> ConnectorDescriptor:
    return _DESCRIPTORS[source_id].model_copy(deep=True)


def connector_descriptors() -> list[ConnectorDescriptor]:
    return [descriptor.model_copy(deep=True) for descriptor in _DESCRIPTORS.values()]


__all__ = (
    "REQUEST_TIMEOUT_SECONDS",
    "connector_descriptor",
    "connector_descriptors",
)
