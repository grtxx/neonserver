import httpx
import uuid
import base64

from websockets import headers

class jsonRPCClient:
    def __init__( self, url, credentials=None ):
        self.url = url
        self.credentials = credentials
        self.credstore = [] 

    def setCredentialsStore( self, credstore ):
        self.credstore = credstore


    async def listTools( self ):
        return await self.call_mcp_jsonrpc( "tools/list", {} )


    async def toolCall( self, method, **params ):
        p = { "name": method, "arguments": params }
        print( f"Calling JSON-RPC MCP tool {method} with params: {params}" )
        return await self.call_mcp_jsonrpc( f"tools/call", p )
        
        
    def injectAuthorizationHeaders( self, args ):
        if self.credentials:
            if ( self.credentials['type'] == "bearer-user" ):
                args["headers"]["Authorization"] = f"Bearer {self.credstore[ self.credentials['token-name'] ]}"
            if ( self.credentials['type'] == "basic" ):
                args["headers"]["Authorization"] = f"Basic {base64.b64encode( self.credentials['static'].encode() ).decode('utf-8')}"                
        return args
    

    async def call_mcp_jsonrpc( self, method: str, params: dict):
        # 1. Construct the JSON-RPC 2.0 payload
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()), # Unique ID to track the response
            "method": method,
            "params": params
        }

        async with httpx.AsyncClient() as client:
            try:
                args = {
                    "json": payload,
                    "headers": {"Content-Type": "application/json"},
                    "timeout": 30.0
                }
                args = self.injectAuthorizationHeaders( args )

                response = await client.post( self.url, **args )
                
                # 3. Handle HTTP errors
                response.raise_for_status()
                
                # 4. Parse the JSON-RPC response
                data = response.json()
                
                if "error" in data:
                    print(f"RPC Error: {data['error']}")
                    return None
                    
                return data.get("result")

            except httpx.HTTPStatusError as e:
                print(f"HTTP Error: {e.response.status_code}")
            except Exception as e:
                print(f"Connection Error: {e}")


def __init__():
    pass