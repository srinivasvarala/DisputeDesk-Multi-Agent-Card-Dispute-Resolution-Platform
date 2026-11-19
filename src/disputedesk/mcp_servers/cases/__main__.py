from disputedesk.mcp_servers.cases.server import DEFAULT_PORT, SERVER_NAME, build_server
from disputedesk.mcp_servers.common.runner import run_server

if __name__ == "__main__":
    run_server(SERVER_NAME, lambda store: build_server(store, store, store), DEFAULT_PORT)
