import os


def database_url() -> str:
    """Assemble the Postgres DSN from discrete environment variables.

    The password arrives via a Kubernetes secretKeyRef, so it is never
    written into a manifest, an image, or this repository. The remaining
    parts are plain config.
    """
    # os.environ[...] raises KeyError when a variable is missing, which is
    # deliberate: a gateway with no database credentials should refuse to
    # start rather than come up half-working. In Kubernetes that surfaces as
    # CrashLoopBackOff, which is a clear signal.
    #
    # These four names must match the keys in the postgres-credentials Secret
    # and the plain env vars in service-1's Deployment. That agreement is the
    # one contract between the manifests and this code.
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ["POSTGRES_HOST"]
    # .get with a default: port is the one value that has a sane fallback.
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ["POSTGRES_DB"]
    # Built here rather than stored as a single DSN env var, so the password
    # stays a discrete Secret key instead of being embedded in a URL that
    # would then be logged wherever the URL is logged.
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def service_2_url() -> str:
    """Cluster-internal DNS name for the worker.

    'service-2' resolves inside the platform namespace with no external
    DNS involved. This is the internal-DNS hop the architecture is
    meant to demonstrate.
    """
    # Defaulted rather than required, because the default is correct in the
    # cluster. Compose overrides it only to prove the override path works.
    return os.environ.get("SERVICE_2_URL", "http://service-2:8000")
