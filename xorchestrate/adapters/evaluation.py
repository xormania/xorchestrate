"""Assessment integrations. Their results supply evidence, never authorization."""

from __future__ import annotations

import http.client
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict

from ..model import JSON, Assessment, OrchestrationError
from ..ports import CapabilityProvider
from .jsonrpc import TelemetryDispatch, capture_origin, observe_telemetry, observe_wire, telemetry_dispatch


class _TelemetryEvaluator:
    def bind_telemetry(self, observer):
        if observer is not None and not callable(observer):
            raise OrchestrationError("Telemetry observer must be callable.", "invalid_configuration")
        previous = getattr(self, "_telemetry_observer", None)
        if isinstance(previous, TelemetryDispatch):
            previous.close()
        self._telemetry_observer = telemetry_dispatch(observer)

    def telemetry_health(self):
        observer = getattr(self, "_telemetry_observer", None)
        return observer.health() if isinstance(observer, TelemetryDispatch) else {"status": "disabled"}

    def drain_telemetry(self, timeout=None):
        observer = getattr(self, "_telemetry_observer", None)
        return observer.drain(timeout) if isinstance(observer, TelemetryDispatch) else {"status": "disabled"}

    def close(self):
        observer = getattr(self, "_telemetry_observer", None)
        if isinstance(observer, TelemetryDispatch):
            observer.close()

    def assess(self, question: str, context: JSON) -> Assessment:
        origin = capture_origin(context)
        observer = getattr(self, "_telemetry_observer", None)
        credential = os.environ.get(self.credential_env, "") if getattr(self, "credential_env", None) else ""
        secrets = (credential,) if credential else ()
        try:
            source_credentials = getattr(getattr(self, "provider", None), "telemetry_credentials", None)
            if callable(source_credentials):
                secrets += tuple(source_credentials())
        except BaseException:
            observe_telemetry(observer, "evaluator.capture_suppressed", {}, {"attempt_id": origin["attempt_id"]},
                              completeness="suppressed", reason="credential_filter_unavailable")
            observer = None
        try:
            try:
                result = self._assess(question, context, observer, origin, secrets)
            except BaseException as error:
                code = getattr(error, "code", "evaluation_unavailable")
                status = "unavailable" if code == "evaluation_unavailable" else "invalid"
                observe_telemetry(observer, "evaluator.validation", {"status": status, "code": code}, origin, secrets=secrets)
                raise
            observe_telemetry(observer, "evaluator.validation", {"status": "valid", "assessment": asdict(result)}, origin, secrets=secrets)
            return result
        finally:
            if isinstance(observer, TelemetryDispatch):
                observer.drain()


def _invalid(message: str) -> OrchestrationError:
    return OrchestrationError(message, "evaluation_invalid")


def _json(value: object, code: str = "invalid_request") -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise OrchestrationError("Assessment data must contain finite JSON values.", code) from None


def _number(value: object, low: float = 0, high: float = 1) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and low <= value <= high


def checked_assessment(value: object) -> Assessment:
    """Validate a provider response without inventing missing confidence or evidence."""
    if not isinstance(value, dict):
        raise _invalid("Assessment must be a JSON object.")
    if not isinstance(value.get("outcome"), str) or not value["outcome"].strip():
        raise _invalid("Assessment needs a nonempty outcome.")
    confidence = value.get("confidence")
    if confidence is not None and not _number(confidence):
        raise _invalid("Assessment confidence must be null or a number from zero to one.")
    if not isinstance(value.get("rationale", ""), str):
        raise _invalid("Assessment rationale must be text.")
    if not isinstance(value.get("evidence", []), list) or not isinstance(value.get("details", {}), dict):
        raise _invalid("Assessment evidence must be a list and details must be an object.")
    # Detach mutable provider values and reject non-JSON evidence/details.
    value = json.loads(_json(value, "evaluation_invalid"))
    return Assessment(value["outcome"], confidence, value.get("rationale", ""),
                      tuple(value.get("evidence", [])), value.get("details", {}))


class ToolEvaluator(_TelemetryEvaluator):
    """Ask an operator-selected capability to assess a question and its context.

    CLI providers return the Assessment object directly. MCP providers may return
    it as structuredContent or as one JSON text block. Additional prose is not
    guessed into a decision. Providers belong to composition and are not closed
    by this adapter, allowing them to be shared with ordinary tool invocation.
    """

    def __init__(self, options: JSON, providers: dict[str, CapabilityProvider]):
        if not isinstance(options, dict) or options.keys() - {"provider", "capability", "arguments"}:
            raise OrchestrationError("Tool evaluator options accept provider, capability and arguments.", "invalid_configuration")
        provider = options.get("provider")
        capability = options.get("capability")
        if not isinstance(provider, str) or provider not in providers or not isinstance(capability, str) or not capability:
            raise OrchestrationError("Tool evaluator needs a configured provider and capability.", "invalid_configuration")
        arguments = options.get("arguments", {})
        if not isinstance(arguments, dict) or arguments.keys() & {"question", "context"}:
            raise OrchestrationError("Evaluator arguments must be an object without question or context overrides.", "invalid_configuration")
        self.provider = providers[provider]
        self.capability = capability
        self.arguments = json.loads(_json(arguments, "invalid_configuration"))

    def _assess(self, question: str, context: JSON, observer, origin, secrets) -> Assessment:
        if not isinstance(question, str) or not question.strip() or not isinstance(context, dict):
            raise OrchestrationError("Assessment requires a question and an object context.")
        arguments = json.loads(_json({**self.arguments, "question": question, "context": context}))
        observe_wire(observer, "evaluator.request", _json(arguments), origin, secrets=secrets, total_bytes=len(_json(arguments)), phase="serialized")
        try:
            matches = [item for item in self.provider.discover() if item.name == self.capability]
            if len(matches) != 1:
                raise OrchestrationError("Assessment capability was not uniquely discovered.", "not_found")
            result = self.provider.invoke(self.capability, arguments, {
                **(origin if observer is not None else {}), "purpose": "assessment", "capability": asdict(matches[0]),
            })
        except OrchestrationError as error:
            raise OrchestrationError("The assessment capability did not return a result.", "evaluation_unavailable",
                                     {"cause": error.code}) from None
        except (OSError, TimeoutError):
            raise OrchestrationError("The assessment capability is unavailable.", "evaluation_unavailable") from None
        observe_telemetry(observer, "evaluator.result", {"result": result}, origin, secrets=secrets)
        if not isinstance(result, dict):
            raise _invalid("Assessment capability must return an object.")
        if result.get("isError"):
            raise OrchestrationError("The assessment capability reported an error.", "evaluation_unavailable")
        if "structuredContent" in result:
            result = result["structuredContent"]
        elif "content" in result and "outcome" not in result:
            content = result["content"]
            if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
                    or content[0].get("type") != "text" or not isinstance(content[0].get("text"), str)):
                raise _invalid("Assessment MCP content needs exactly one JSON text block or structuredContent.")
            try:
                result = json.loads(content[0]["text"])
            except (ValueError, RecursionError):
                raise _invalid("Assessment capability returned invalid JSON text.") from None
        return checked_assessment(result)

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


class TypeSafeEvaluator(_TelemetryEvaluator):
    """TypeSafe choice assessment using an explicitly configured model and rubric.

    The wire format follows the reusable TypeSafe integration in the imported
    Agentscient devtools/jev.py. No imported defaults, role policy, retries, model
    naming restrictions or credential machinery are retained.
    """

    def __init__(self, options: JSON):
        allowed = {"endpoint", "model", "criteria", "credential_env", "timeout", "max_request_bytes", "max_response_bytes", "allow_http"}
        if not isinstance(options, dict) or options.keys() - allowed:
            raise OrchestrationError("TypeSafe evaluator contains an unknown configuration field.", "invalid_configuration")
        endpoint = options.get("endpoint")
        if not isinstance(endpoint, str):
            raise OrchestrationError("TypeSafe needs an endpoint URL.", "invalid_configuration")
        try:
            parsed = urllib.parse.urlsplit(endpoint)
            _ = parsed.port
        except ValueError:
            raise OrchestrationError("TypeSafe endpoint is malformed.", "invalid_configuration") from None
        if type(options.get("allow_http", False)) is not bool:
            raise OrchestrationError("allow_http must be a boolean.", "invalid_configuration")
        schemes = {"https", "http"} if options.get("allow_http") else {"https"}
        if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise OrchestrationError("TypeSafe requires a valid HTTPS endpoint without embedded credentials; local HTTP is an explicit option.", "invalid_configuration")
        model, criteria = options.get("model"), options.get("criteria")
        if not isinstance(model, str) or not model.strip():
            raise OrchestrationError("TypeSafe model must be explicitly configured.", "invalid_configuration")
        if (not isinstance(criteria, dict) or not criteria
                or any(not isinstance(k, str) or not k or not isinstance(v, (str, dict, list, type(None))) for k, v in criteria.items())):
            raise OrchestrationError("TypeSafe criteria must map outcome names to rubric descriptions.", "invalid_configuration")
        credential = options.get("credential_env")
        if credential is not None and (not isinstance(credential, str) or not credential or "=" in credential or "\0" in credential):
            raise OrchestrationError("credential_env must name an environment variable.", "invalid_configuration")
        timeout = options.get("timeout", 30)
        if not _number(timeout, 0, float("inf")) or timeout == 0:
            raise OrchestrationError("TypeSafe timeout must be a positive finite number.", "invalid_configuration")
        self.limits = {}
        for name, default in (("max_request_bytes", 1048576), ("max_response_bytes", 1048576)):
            value = options.get(name, default)
            if type(value) is not int or value <= 0:
                raise OrchestrationError(f"{name} must be a positive integer.", "invalid_configuration")
            self.limits[name] = value
        self.endpoint, self.model, self.timeout, self.credential_env = endpoint, model, timeout, credential
        self.criteria = json.loads(_json(criteria, "invalid_configuration"))

    def _assess(self, question: str, context: JSON, observer, origin, secrets) -> Assessment:
        if not isinstance(question, str) or not question.strip() or not isinstance(context, dict):
            raise OrchestrationError("Assessment requires a question and an object context.")
        credential = os.environ.get(self.credential_env, "") if self.credential_env else ""
        if self.credential_env and not credential:
            raise OrchestrationError("The configured assessment credential is unavailable.", "evaluation_unavailable", {"cause": "credentials_unavailable"})
        payload = _json({"model": self.model, "state": context,
                         "questions": {"assessment": {"type": "choice", "instructions": question, "criteria": self.criteria}}})
        observe_wire(observer, "evaluator.request", payload[:self.limits["max_request_bytes"] + 1], origin, secrets=secrets,
                     complete=len(payload) <= self.limits["max_request_bytes"] + 1, total_bytes=len(payload),
                     phase="serialized", model=self.model)
        # Never echo credentials into model inputs, evidence or error details.
        if credential and credential in payload.decode("utf-8"):
            raise OrchestrationError("Assessment input contains the configured credential.", "invalid_request")
        if len(payload) > self.limits["max_request_bytes"]:
            raise OrchestrationError("Assessment exceeds the configured request budget.", "invalid_request")
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Xorchestrate"}
        if credential:
            if "\r" in credential or "\n" in credential:
                raise OrchestrationError("The configured assessment credential is invalid.", "evaluation_unavailable")
            headers["Authorization"] = "Bearer " + credential
        request = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
        captured = bytearray()
        response_observed = False

        def read_response(response, deadline=None):
            nonlocal response_observed
            maximum = self.limits["max_response_bytes"]
            status = getattr(response, "status", getattr(response, "code", None))
            total = None
            try:
                length = response.headers.get("Content-Length")
                if length is not None and str(length).isdigit():
                    total = int(length)
            except (AttributeError, ValueError, TypeError):
                pass
            reason = None
            complete = False
            try:
                # read1 exposes each available body block before a later timeout;
                # a fallback supports the same small response interface as before.
                read_one = getattr(response, "read1", None)
                read = read_one if callable(read_one) else response.read
                while len(captured) <= maximum:
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            reason = "capture_deadline"
                            raise TimeoutError("Assessment diagnostic capture deadline reached")
                        # urllib's HTTPError wraps an HTTPResponse. Bound its
                        # next socket read by the one original request deadline,
                        # rather than granting every body chunk a fresh timeout.
                        stream = response
                        for _ in range(3):
                            raw = getattr(stream, "raw", None)
                            connection = getattr(raw, "_sock", None)
                            if connection is not None:
                                connection.settimeout(remaining)
                                break
                            stream = getattr(stream, "fp", None)
                        else:
                            reason = "capture_deadline_unavailable"
                            raise TimeoutError("Assessment diagnostic read cannot be bounded")
                    count = min(65536, maximum + 1 - len(captured))
                    # A read(n) fallback may internally wait for n bytes across
                    # many successful socket reads. One byte keeps that fallback
                    # within the next remaining-time timeout as well.
                    block = read(1 if deadline is not None and not callable(read_one) else count)
                    if not block:
                        complete = True
                        if total is None:
                            total = len(captured)
                        break
                    captured.extend(block)
                if len(captured) > maximum:
                    reason = "response_limit"
            except http.client.IncompleteRead as error:
                captured.extend(error.partial[:max(0, maximum + 1 - len(captured))])
                reason = "interrupted"
                raise
            except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
                reason = reason or "read_interrupted"
                raise
            finally:
                response_observed = True
                observe_wire(observer, "evaluator.response", bytes(captured), origin, secrets=secrets,
                             complete=complete and len(captured) <= maximum, total_bytes=total,
                             reason=reason, http_status=status, model=self.model)
            return bytes(captured)

        deadline = time.monotonic() + self.timeout
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                body = read_response(response)
        except urllib.error.HTTPError as error:
            status = error.code
            try:
                if observer is not None:
                    read_response(error, deadline)
            except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
                pass
            finally:
                error.close()
            raise OrchestrationError("The assessment provider returned an HTTP error.", "evaluation_unavailable", {"status": status}) from None
        except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
            if not response_observed:
                observe_wire(observer, "evaluator.response", bytes(captured), origin, secrets=secrets,
                             complete=False, reason="connection_unavailable", model=self.model)
            raise OrchestrationError("The assessment provider could not be reached.", "evaluation_unavailable") from None
        if len(body) > self.limits["max_response_bytes"]:
            raise _invalid("Assessment response exceeds the configured response budget.")
        if credential and credential.encode() in body:
            raise _invalid("Assessment provider returned credential material.")
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise _invalid("Assessment provider returned invalid JSON.") from None
        if not isinstance(value, dict) or value.get("model") != self.model:
            raise _invalid("Assessment provider returned an unexpected model.")
        answers = value.get("answers")
        if not isinstance(answers, dict) or answers.keys() != {"assessment"}:
            raise _invalid("Assessment provider returned an incomplete answer set.")
        answer = answers["assessment"]
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise _invalid("Assessment provider returned an invalid answer type.")
        probabilities = answer.get("probabilities")
        if (not isinstance(probabilities, dict) or probabilities.keys() != self.criteria.keys()
                or not all(_number(v) for v in probabilities.values()) or abs(sum(probabilities.values()) - 1) > 0.01
                or not isinstance(answer.get("choice"), str) or answer["choice"] not in self.criteria
                or not _number(answer.get("confidence"))):
            raise _invalid("Assessment provider returned an invalid choice distribution.")
        details = {"model": self.model, "probabilities": probabilities}
        if "usage" in value:
            usage = value["usage"]
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")):
                raise _invalid("Assessment provider returned invalid usage data.")
            details["usage"] = {key: usage[key] for key in ("input_tokens", "output_tokens")}
            observe_telemetry(observer, "evaluator.usage", {"usage": usage, "counter_semantics": "incremental",
                              "usage_stream_id": origin["attempt_id"], "source_version": "typesafe-choice-v1"},
                              origin, secrets=secrets, event_id="evaluator-usage:" + origin["attempt_id"])
        return checked_assessment({"outcome": answer["choice"], "confidence": answer["confidence"], "details": details})
