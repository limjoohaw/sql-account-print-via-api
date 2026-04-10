"""SQL Account REST API client with AWS SigV4 authentication.

Adapted from the official wiki BaseApiClient at wiki.sql.com.my/wiki/Restful_API.
Uses boto3.Session + botocore.auth.SigV4Auth for request signing.
"""

import urllib.parse

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


class SQLAccAPIClient:
    """SigV4-authenticated client for SQL Account REST API.

    Mirrors the wiki's BaseApiClient._send_request pattern exactly:
    boto3.Session → AWSRequest → SigV4Auth.add_auth → requests.request
    """

    def __init__(self, host: str, region: str, access_key: str, secret_key: str,
                 service: str = "execute-api"):
        self.host = host
        self.region = region
        self.service = service
        self.base_url = f"https://{host}"
        self.session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        # Wiki sets Host + Content-Type as default headers
        self.default_headers = {
            "Host": host,
            "Content-Type": "application/json",
        }

    def _send_request(self, method: str, full_url: str, payload: str = None,
                      custom_headers: dict = None, stream: bool = False,
                      timeout: int = 60) -> requests.Response:
        """Send an authenticated request. Mirrors wiki BaseApiClient._send_request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            full_url: Complete URL including query params
            payload: JSON string for POST/PUT
            custom_headers: Override/extend default headers (e.g. PDF Content-Type)
            stream: If True, response body is streamed (for PDF downloads)
            timeout: Request timeout in seconds

        Returns:
            requests.Response

        Raises:
            requests.HTTPError: On 4xx/5xx responses
            requests.ConnectionError: On network failures
        """
        request_data = payload.encode("utf-8") if payload else b""

        headers = self.default_headers.copy()
        if custom_headers:
            headers.update(custom_headers)

        aws_req = AWSRequest(
            method=method,
            url=full_url,
            headers=headers,
            data=request_data,
        )
        SigV4Auth(
            self.session.get_credentials(), self.service, self.region
        ).add_auth(aws_req)

        response = requests.request(
            method,
            aws_req.url,
            headers=dict(aws_req.headers),
            data=request_data,
            timeout=timeout,
            stream=stream,
        )
        response.raise_for_status()
        return response

    def fetch_document_json(self, resource: str, docno: str) -> dict:
        """Step 1: Fetch document by docno → JSON with dockey.

        Uses the wiki's /*? pattern for detail records (returns master + detail).
        GET /<resource>/*?docno=<docno>
        """
        url = f"{self.base_url}/{resource}/*?docno={urllib.parse.quote(docno, safe='')}"
        response = self._send_request("GET", url)
        return response.json()

    def fetch_document_pdf(self, resource: str, dockey, template_name: str,
                           stream: bool = True) -> requests.Response:
        """Step 2: Fetch document as PDF using dockey + template name.

        GET /<resource>/{dockey} with Content-Type: application/pdf;template=<name>
        Returns streamed response — caller reads via .iter_content(chunk_size=8192).
        Mirrors the wiki's get_SLIV_PDF flow.
        """
        url = f"{self.base_url}/{resource}/{dockey}"
        return self._send_request(
            "GET", url,
            custom_headers={
                "Content-Type": f"application/pdf;template={template_name}",
            },
            stream=stream,
        )

    def health_check(self) -> bool:
        """Quick check: GET /version to verify API service is reachable."""
        try:
            url = f"{self.base_url}/version"
            self._send_request("GET", url, timeout=10)
            return True
        except Exception:
            return False


def get_field_value(response_data, fieldname: str):
    """Recursively find a field value in nested JSON. From wiki APICommon."""
    if isinstance(response_data, dict):
        for key, value in response_data.items():
            if key == fieldname:
                return value
            result = get_field_value(value, fieldname)
            if result is not None:
                return result
    elif isinstance(response_data, list):
        for item in response_data:
            result = get_field_value(item, fieldname)
            if result is not None:
                return result
    return None
