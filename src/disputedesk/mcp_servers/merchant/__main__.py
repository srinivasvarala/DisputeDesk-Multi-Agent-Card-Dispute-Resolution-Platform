from disputedesk.mcp_servers.common.runner import run_server
from disputedesk.mcp_servers.merchant.server import DEFAULT_PORT, SERVER_NAME, build_server
from disputedesk.security.agent_tokens import NO_AUTH

if __name__ == "__main__":
    run_server(
        SERVER_NAME, lambda store: build_server(store, store, verifier=NO_AUTH), DEFAULT_PORT
    )
