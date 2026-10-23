#!/usr/bin/env python3
"""Real PHP acceptance for Mate and native MCP attributes in a disposable project.

Requires this repository's reviewed Composer lock already installed. Copies only
application/tooling assets and dependencies; no live lane records or credentials.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ops/conductor/tools"))
from session_adapters.channel import Channel  # noqa: E402

from ops.testkit import World, run  # noqa: E402

SERVICE = r'''<?php
namespace App\Service;
use Mcp\Capability\Attribute\McpTool;
final class Greeting
{
    public function __construct(private GreetingFormatter $formatter) {}

    #[McpTool(name: 'greet', description: 'Greet a person.')]
    public function greet(string $name, int $repeat = 1): array
    {
        if ($repeat < 1) { throw new \InvalidArgumentException('repeat must be positive'); }
        return ['message' => str_repeat($this->formatter->format($name), $repeat)];
    }
}
'''
FORMATTER = r'''<?php
namespace App\Service;
final class GreetingFormatter
{
    public function format(string $name): string { return 'Hello '.$name; }
}
'''
CONTROLLER = r'''<?php
namespace App\Controller;
use App\Service\Greeting;
use Symfony\Bundle\FrameworkBundle\Controller\AbstractController;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\Routing\Attribute\Route;
final class GreetingController extends AbstractController
{
    public function __construct(private Greeting $greeting) {}
    #[Route('/_smoke/greeting', name: 'smoke_greeting', methods: ['GET'])]
    public function __invoke(Request $request): JsonResponse
    {
        return new JsonResponse($this->greeting->greet($request->query->get('name', 'World')));
    }
}
'''
TEST = r'''<?php
use App\Kernel;
use App\Service\Greeting;
use Mcp\Capability\Registry\ReferenceHandler;
use Mcp\Schema\JsonRpc\Response;
use Mcp\Schema\Request\CallToolRequest;
use Mcp\Server\Handler\Request\CallToolHandler;
use Mcp\Server\Session\SessionInterface;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\Request;
final class ScenarioTest extends TestCase
{
    public function testAttributeExposesTheAutowiredServiceThroughMcp(): void
    {
        $kernel = new Kernel('test', true);
        try {
            $kernel->boot();
            $container = $kernel->getContainer()->get('test.service_container');
            $container->get('mcp.server.default.builder')->build();
            $registry = $container->get('mcp.server.default.registry');
            $tool = $registry->getTool('greet')->tool;
            self::assertSame('string', $tool->inputSchema['properties']['name']['type']);
            self::assertSame('integer', $tool->inputSchema['properties']['repeat']['type']);
            $request = CallToolRequest::fromArray([
                'jsonrpc' => '2.0', 'id' => 1, 'method' => 'tools/call',
                'params' => ['name' => 'greet', 'arguments' => ['name' => 'River', 'repeat' => 2]],
            ]);
            $response = (new CallToolHandler($registry, new ReferenceHandler($container)))
                ->handle($request, $this->createStub(SessionInterface::class));
            self::assertInstanceOf(Response::class, $response);
            self::assertSame(['message' => 'Hello RiverHello River'], $response->result->structuredContent);
            $this->expectException(\InvalidArgumentException::class);
            $container->get(Greeting::class)->greet('River', 0);
        } finally { $kernel->shutdown(); }
    }

    public function testRequestProfileDescribesAnActualRequest(): void
    {
        $kernel = new Kernel('test', true);
        try {
            $kernel->boot();
            $profiler = $kernel->getContainer()->get('profiler');
            $profiler->enable();
            $request = Request::create('/_smoke/greeting?name=River');
            $request->headers->set('Authorization', 'Bearer synthetic-profile-secret');
            $request->cookies->set('session', 'synthetic-cookie-secret');
            $response = $kernel->handle($request);
            $kernel->terminate($request, $response);
            self::assertSame(200, $response->getStatusCode());
            self::assertSame(['message' => 'Hello River'], json_decode($response->getContent(), true));
            self::assertNotNull($profiler->loadProfileFromResponse($response));
        } finally { $kernel->shutdown(); }
    }
}
'''


def require(condition, evidence="Missing expected integration result"):
    if not condition:
        raise AssertionError(evidence)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--php", required=True)
    args = parser.parse_args()
    php = str(Path(args.php).absolute())
    if not (ROOT / "vendor/autoload.php").is_file():
        raise SystemExit("Install the reviewed Composer lock first; this smoke does not install dependencies.")
    with World(prefix="symfony-mcp-") as world:
        root = world.cwd
        for name in ("vendor", "src", "config", "mate", "bin", "public", "packages"):
            shutil.copytree(ROOT / name, root / name)
        for name in ("composer.json", "composer.lock", "symfony.lock", ".env", ".env.test", ".gitignore"):
            shutil.copy2(ROOT / name, root / name)
        world.plant_text(root / "src/Service/Greeting.php", SERVICE)
        world.plant_text(root / "src/Service/GreetingFormatter.php", FORMATTER)
        world.plant_text(root / "src/Controller/GreetingController.php", CONTROLLER)
        world.plant_text(root / "tests/bootstrap.php", (ROOT / "tests/bootstrap.php").read_text())
        world.plant_text(root / "tests/ScenarioTest.php", TEST)
        world.plant_text(root / "phpunit.xml", '<phpunit bootstrap="tests/bootstrap.php" cacheDirectory=".phpunit.cache" failOnWarning="true"><testsuites><testsuite name="scenario"><directory>tests</directory></testsuite></testsuites></phpunit>')
        conf = world.root / "lane.conf"
        conf.write_text("LANE_NAME='symfony-smoke'\n")
        config = world.root / "project.json"
        config.write_text(json.dumps({"version": 1, "provider": "symfony", "php": php, "environment": "test"}))
        env = world.env(extra={"LANE_CONF": str(conf), "WF_AGENT": "Codex <test@example.test>",
                               "CLAUDE_CODE_SESSION_ID": "smoke", "COORDINATION_CAPABILITY": "a" * 64})
        for command in (["git", "init", "-q", "-b", "work"], ["git", "config", "user.name", "Fixture"],
                        ["git", "config", "user.email", "fixture@example.test"], ["git", "add", "."],
                        ["git", "commit", "-qm", "fixture"],
                        [sys.executable, "-B", str(ROOT / "ops/conductor/tools/coordination.py"), "bind", "--roles", "worker"]):
            result = run(command, cwd=root, env=env, timeout=60)
            if result.exit:
                raise AssertionError(result.stderr)
        def open_entry():
            connection = Channel([sys.executable, "-B", str(ROOT / "ops/conductor/tools/dev_mcp.py"),
                                  "--workspace", str(root), "--project-config", str(config), "serve"], env=env, cwd=root)
            initialized = connection.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "symfony-smoke", "version": "1"}}, timeout=30)
            require("project_context" in initialized["instructions"], "Guidance is absent from MCP initialization")
            connection.notify("notifications/initialized")
            return connection

        rpc = open_entry()
        try:
            startup_tools = rpc.request("tools/list", timeout=30)["tools"]
            require(len(startup_tools) == 3, "Initial tool surface expanded")

            def call(name, arguments=None, *, success=True):
                response = rpc.request("tools/call", {"name": "capability_invoke", "arguments": {
                    "capability": name, "arguments": arguments or {}}}, timeout=220)
                require(len(json.dumps(response).encode()) <= 16384, "Unbounded response")
                data = response.get("structuredContent", response)
                if success and (response.get("isError") or data.get("outcome", "success") != "success"):
                    raise AssertionError(f"{name}: {json.dumps(data)}")
                return data

            state = call("project_status")
            require(state["container"]["state"] == "unavailable", state)
            context = call("project_context")["data"]
            require(context["php"].startswith("8.5."), context)
            locked = json.loads((root / "composer.lock").read_text())
            framework = next(p["version"] for p in locked["packages"] if p["name"] == "symfony/framework-bundle")
            require(context["installed_packages"]["symfony/framework-bundle"] == framework, context)
            require("Mcp\\Capability\\Attribute\\McpTool" in context["guidance"], context)
            require(call("code_status")["development_context"]["capability"] == "project_context")
            skills = call("mate_skills", {"query": "service-inspection"})["data"]["skills"]
            require(len(skills) == 1, skills)
            skill = call("mate_skill", {"name": skills[0]["installed_name"]})
            require(skill.get("data", {}).get("package_version") == context["installed_packages"]["symfony/ai-symfony-mate-extension"], skill)
            require("constructor" in skill["data"]["body"].lower(), skill)
            invalid_skill = call("mate_skill", {"name": "../../outside"}, success=False)
            require("isError" in invalid_skill or "refused" in json.dumps(invalid_skill), invalid_skill)
            rpc.close()
            rpc = open_entry()
            require(call("project_context")["data"]["guidance_sha256"] == context["guidance_sha256"],
                    "Context delivery must survive an entry restart")
            tools = call("mate_tools")["data"]["tools"]
            require(any(row["name"] == "symfony-services" for row in tools), tools)
            require(call("mate_describe", {"tool": "symfony-service-detail"})["data"])
            require(call("mate_environment")["data"])
            require(call("mate_services", {"query": "Greeting"}, success=False)["outcome"] == "failed")
            require(call("project_status")["container"]["state"] == "unavailable")
            call("project_refresh", {"expected_source_fingerprint": state["source_fingerprint"]})
            services = call("mate_services", {"query": "Greeting"})
            require(services["freshness"]["state"] == "current", services)
            require("App\\Service\\Greeting" in json.dumps(services["data"]).replace("\\\\", "\\"), services)
            service = call("mate_service", {"id": "App\\Service\\Greeting"})
            require("GreetingFormatter" in json.dumps(service), "Mate must report the actual constructor wiring")
            config_file = root / "config/services.yaml"
            config_file.write_text(config_file.read_text() + "\n    greeting.alias: '@App\\Service\\Greeting'\n")
            stale = call("mate_services", {"query": "greeting.alias"})
            require(stale["freshness"]["state"] == "stale" and stale["data"]["untrusted_data"]["count"] == 0, stale)
            call("project_refresh", {"expected_source_fingerprint": stale["source_fingerprint"]})
            services = call("mate_services", {"query": "greeting.alias"})
            require(services["data"]["untrusted_data"]["count"] == 1, services)
            checked = call("project_check", {"expected_source_fingerprint": services["source_fingerprint"]})
            require(checked["outcome"] == "success", checked)
            profiles = call("mate_profiles", {"url": "/_smoke/greeting", "limit": 1})
            token = profiles["data"]["untrusted_data"]["profiles"][0]["token"]
            profile = call("mate_profile", {"token": token})
            require(profile["freshness"]["state"] == "historical", profile)
            resource = call("mate_resource", {"token": token, "collector": "request"})
            raw = json.dumps(resource)
            if resource.get("truncated"):
                offset, raw = 0, ""
                while offset is not None:
                    page = call("project_output", {"result": resource["result"], "offset": offset})
                    raw += page["untrusted_text"]
                    offset = page["next_offset"]
            require("_security_notice" in raw, raw)
            require("synthetic-profile-secret" not in raw and "synthetic-cookie-secret" not in raw, resource)
            # MCP and HTTP both use the autowired service. Break its dependency
            # to prove these assertions exercise behavior, not just registration.
            formatter = root / "src/Service/GreetingFormatter.php"
            formatter.write_text(FORMATTER.replace("'Hello '.$name", "'wrong'"))
            state = call("project_status")
            failed = call("project_check", {"expected_source_fingerprint": state["source_fingerprint"]}, success=False)
            require(failed["outcome"] == "failed", failed)
            require("symfony_checks" not in call("project_status")["verified_at_current_source"])
            print(json.dumps({"status": "passed", "php": php,
                              "tool_schema_bytes": len(json.dumps({"tools": startup_tools}, sort_keys=True, separators=(",", ":")).encode()),
                              "checks": ["real_mate", "mcp_context_and_skill_delivery", "container_refresh", "stale_config_refresh", "native_mcp_attribute_autowiring",
                                         "profile_capture_redaction", "planted_behavior_failure"],
                              "native_harness": "not exercised"}, indent=2))
        finally:
            rpc.close()


if __name__ == "__main__":
    main()
