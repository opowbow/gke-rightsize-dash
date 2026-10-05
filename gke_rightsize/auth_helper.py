"""Authentication helper for Google Cloud Monitoring and Vertex AI / GenAI.

Ensures that authentication seamlessly works with Application Default Credentials
and falls back to Compute Engine metadata credentials when running in environments
like Google Cloud Shell or GCE VM instances where user ADC tokens may have expired.
"""

import os
import logging
import google.auth
import google.auth.compute_engine
import google.auth.transport.requests

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
DEFAULT_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")


def get_google_credentials():
    """Returns valid Google Cloud credentials and project ID.

    Tries Application Default Credentials first. If token refresh fails,
    falls back to Compute Engine metadata credentials and patches
    google.auth.default so that subsequent SDK calls (like google-genai)
    use the active Compute Engine credentials.
    """
    explicit_project = os.environ.get("GOOGLE_CLOUD_PROJECT")

    try:
        creds, detected_project = google.auth.default()
        req = google.auth.transport.requests.Request()
        creds.refresh(req)
        project_id = explicit_project or detected_project or ""
        if not project_id:
            logger.warning(
                "No Google Cloud project detected. Set the GOOGLE_CLOUD_PROJECT "
                "environment variable or run 'gcloud config set project <PROJECT_ID>'."
            )
        return creds, project_id
    except Exception as e:
        logger.info(f"ADC refresh failed ({e}); falling back to Compute Engine credentials.")
        creds = google.auth.compute_engine.Credentials()
        project_id = explicit_project or ""
        # Patch google.auth.default so google-adk / google-genai / vertexai pick it up
        google.auth.default = lambda *args, **kwargs: (creds, project_id)
        return creds, project_id


def setup_environment():
    """Sets standard environment variables for Vertex AI and project."""
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "1")
    os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ.get("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)
    creds, project_id = get_google_credentials()
    if project_id and "GOOGLE_CLOUD_PROJECT" not in os.environ:
        os.environ["GOOGLE_CLOUD_PROJECT"] = project_id

