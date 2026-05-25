from types import coroutine

from pydantic import config, json
from chatsessiondata import ChatSessionData
from configmanager import conf, log
from configmanager import get_tools, get_params_for_tool
from fastapi import FastAPI, Request # type: ignore
from langchain_core.tools import Tool # type: ignore
from jsonmcp_client import jsonRPCClient
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage # type: ignore
from mcp import ClientSession # type: ignore
from langchain_google_genai import ChatGoogleGenerativeAI # type: ignore
from langchain_core.messages import ToolMessage # type: ignore
from contextlib import asynccontextmanager # type: ignore
from typing import Callable, Iterable
import json
import inspect


class AIChat:

    def __init__( self, app, llm: ChatGoogleGenerativeAI | None=None, activeHistorySize=15, onApproveCallback: Callable | None = None ):
        self.config = config
        self.db_pool = None
        self.tools = None
        self.session_data = ChatSessionData( app )
        self._sid = ""
        self.titleGenerationInProgress = False
        self.activeHistorySize = activeHistorySize
        self.messages= [];
        self.app = app
        self.approveCallback = onApproveCallback
        self._llm = None
        self.max_tool_calls = 8


    def getLLM( self ):
        if self is not None:
            if self._llm is None:
                llm = ChatGoogleGenerativeAI(
                    model = self.session_data.settings["model"],
                    google_api_key=self.session_data.settings["modelConfig"]['apikey']
                )
                self._llm = llm.bind_tools( self.session_data.tools )
        return self._llm


    async def loadChat( self, sid: str ):
        self._sid = sid
        await self.session_data.load( sid )
        self.messages = await self.session_data.getLastMessages( self.activeHistorySize ) # type: ignore
        self.messages.insert( 0, SystemMessage( content=await self.session_data.getCustomisedSystemPrompt() ) )


    async def shiftActiveHistory( self ):
        if len( self.session_data.messages ) > 1:
            while ( len(self.messages) > self.activeHistorySize ) or not isinstance(self.messages[1], HumanMessage): # type: ignore
                self.messages.pop(1)  # Az első üzenet a rendszerüzenet, azt nem töröljük
                if len(self.messages) <= 1:
                    break
        self.messages[0] = SystemMessage( content=await self.session_data.getCustomisedSystemPrompt() )


    async def createChat( self, personality: str = "default" ) -> str:
        self._sid = await self.session_data.createChat( personality )
        return self._sid


    async def getHistory( self ):
        async for msg in self.session_data.getHistory():
            yield msg


    def setUserdata( self, userdata: dict ):
        self.session_data.settings["userdata"] = userdata


    def setCredentials( self, credentials: dict ):
        self.session_data.settings["credentials"] = credentials


    def getSid( self ):
        return self._sid


    def getName( self ):
        return self.session_data.name


    async def save( self ):
        await self.session_data.save()
    

    async def generateTitle( self ):
        if ( self.getLLM() is None ):
            return None
        if len( self.session_data.messages ) < 8 or self.session_data.name != "":
            return None
        if self.titleGenerationInProgress:
            return None
        self.titleGenerationInProgress = True
        msgs = ""
        for msg in self.session_data.messages:
            if isinstance(msg, HumanMessage):
                msgs = msgs + "\n\n" + msg.content # type: ignore
        prompt = f"Generate a maximum 8 word title for this conversation:\n\n'{msgs}'\n\nReturn only the title in the conversation's language!"
        
        res = await self.getLLM().ainvoke([("human", prompt)]) # type: ignore
        new_title = res.content[0]["text"].strip() # type: ignore
        self.session_data.name = new_title
        await self.session_data.save()
        self.titleGenerationInProgress = False
        return new_title


    def normalize_params( self, args: dict, toolparams: dict ) -> dict:
        finalArguments = args
        if ( '__arg1' in args and len(args) == 1 and len(toolparams['inputSchema']['properties']) > 1 ):
            try:
                if isinstance(args['__arg1'], dict):
                    args = args['__arg1']
                else:
                    oargs = args
                    args = json.loads( args['__arg1'] )
                    if ( not isinstance(args, dict) ):
                        args = oargs
            except:
                pass
        if ( '__arg1' in args ):
            cnt = 0
            args2 = {}
            for k in toolparams['inputSchema']['properties'].keys():
                cnt = cnt + 1
                if ( ( '__arg%d' % cnt ) in args ):
                    if k == "ID":
                        args2[ k ] = int(args[ '__arg%d' % cnt ])
                    else:
                        args2[ k ] = args[ '__arg%d' % cnt ]
            finalArguments = args2
        return finalArguments


    async def call_mcp_tool_streamableHttp( self, name: str, args: dict, toolparams: dict, progress=None ):
#       try:
            sseClient = self.session_data.getConfiguredStreamableHttpClient( toolparams )

            async with sseClient as (read, write, _):
                async with ClientSession(read, write) as session:
                    args = self.normalize_params( args, toolparams ) # type: ignore
                    await session.initialize()
                    result = session.call_tool( name, arguments=args )
                    if inspect.isasyncgen( result ):
                        async for chunk in result: # type: ignore
                            if chunk.content and hasattr(chunk.content[0], 'text'):
                                yield chunk.content[0].text # type: ignore
                    if inspect.isawaitable( result ):
                        res = await result
                        yield res


    async def call_mcp_tool_sse(self, name: str, args: dict, toolparams: dict, progress=None ):
#        try:
            sseClient = self.session_data.getConfiguredSSEClient( toolparams )

            async with sseClient as (read, write):
                async with ClientSession(read, write) as session:
                    args = self.normalize_params( args, toolparams ) # type: ignore
                    await session.initialize()
                    result = session.call_tool( name, arguments=args )
                    async for chunk in result: # type: ignore
                        if chunk.content and hasattr(chunk.content[0], 'text'):
                            yield chunk.content[0].text # type: ignore
#        except Exception as e:
#           log.error(f"Tool not available: {e}")


    async def call_mcp_tool_jsonrpc( self, name: str, args: dict, toolparams: dict, progress=None ):
        try:
            jsonrpcClient = self.session_data.getConfiguredJsonRPCClient( toolparams )
            args = self.normalize_params( args, toolparams ) # type: ignore
            result = await jsonrpcClient.toolCall( name, **args ) # type: ignore
            yield result
        except Exception as e:
            log.error(f"Tool not available: {e}")
            yield f"Tool not found: '{name}'"


    def getToolParams( self, tool_name: str) -> dict | None:
        return self.app.state.toolmap.get( tool_name )


    async def call_mcp_tool(self, name: str, args: dict, progress=None ):    
        toolparams = self.getToolParams( name )

        gen = None # type: ignore
        if toolparams['proto'] == 'sse':  # type: ignore
            gen = self.call_mcp_tool_sse( name, args, toolparams, progress ) # type: ignore

        elif 'proto' in toolparams and toolparams['proto'] == 'jsonrpc': # type: ignore
            gen =  self.call_mcp_tool_jsonrpc( name, args, toolparams, progress ) # type: ignore

        elif 'proto' in toolparams and toolparams['proto'] == 'streamablehttp': # type: ignore
            gen = self.call_mcp_tool_streamableHttp(name, args, toolparams, progress ) # type: ignore

        if inspect.isasyncgen( gen ):
            result = None
            async for res in gen:
                if( result is None ):
                    result = res
                else:
                    result += res   
            return result

        if inspect.isawaitable( gen ):
            gen = await gen
            result = ""
            try:
                resJson = json.loads( gen ) # type: ignore
            except:
                return result
                    
            if "approvalToken" in resJson:
                approvalToken = resJson.get( "approvalToken", "" )
                #self.session_data.createApproval( name, args=args, aptoken = approvalToken, toolResult=resJson )
            else:
                return result          


    async def ApproveAndContinue( self, approval_id, useranswer ):
        approval = await self.session_data.getApproval( approval_id ) # type: ignore
        approvalToken = approval['args'].get( "approvalToken", "" )

        if ( not ( "type" in useranswer and "action" in useranswer ) ):
            # invalid response, break the loop
            return
        if "overrides" in useranswer:
            for k in toolparams['inputSchema']['properties'].keys(): # type: ignore
                if ( k in useranswer["overrides"] ):
                    approval['args'][ k ] = useranswer["overrides"][ k ]
        approval['args']["approvalToken"] = approvalToken

        observation = self.call_mcp_tool( approval["name"], approval["args"] ) # , progress=lambda msg: websocket.send_json( { "type": "toolcall", "content": f"{msg}" } )
        
        async for ob in observation: # type: ignore
            t_msg = ToolMessage( content=str(ob), tool_call_id=approval["tool_call_id"] )
            await self.session_data.saveMessage( t_msg )
            self.messages.append( t_msg )

        yield self.Continue()


    async def Continue( self ):
        if self.getLLM() is None:
            raise Exception( "LLM not configured" )

        await self.shiftActiveHistory()

        # conditional title generation
        if ( len(self.session_data.messages) > 8 and self.session_data.name == "" ):
            chatTitle = await self.generateTitle()
            if chatTitle is not None:
                yield {"type": "title", "content": chatTitle}

        # question/anwering logic
        #res = await self.getLLM().ainvoke( [ ( "human", question.strip() ) ]) # type: ignore

        toolCalls = 0
        
        while True:
            tool_calls_in_answer = []
            ai_msg = None
            lastcontent = ""
            laststate = 0

            async for event in self.getLLM().astream_events( self.messages, version="v2" ): # type: ignore
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"] # type: ignore
                        
                    if hasattr( chunk, "tool_calls"):
                        for tool_call in chunk.tool_calls: # type: ignore
                            tool_calls_in_answer.append(tool_call)

                        if ai_msg is None:
                            ai_msg = chunk
                        else:
                            ai_msg += chunk
                        if ( chunk.content[:-len(lastcontent)] == lastcontent ):
                            chunk.content = chunk.content[-len(lastcontent):] # type: ignore

                        async for cmd in self.session_data.extractCommands( chunk.content, laststate, exec=False ): 
                            if ( cmd["type"] == "result" ):
                                lastcontent = cmd["content"]
                                laststate = cmd["commandMode"]
                                break
                        content = chunk.content # type: ignore
                        if content:
                            yield {"type": "token", "content": lastcontent }

            if ai_msg:
                if ai_msg.content != '':
                    yield { 'type': 'done' }
                self.messages.append( ai_msg )
                await self.session_data.saveMessage( ai_msg )
                async for cmd in self.session_data.extractCommands( ai_msg.content, 0, exec=True ):
                    if ( cmd["type"] == "uicommand" ):
                        yield cmd["content"]
                    
                # Ha vannak tool hívások, mindegyiket végrehajtjuk
                tcnt = 0
                for tool_call in tool_calls_in_answer: # type: ignore                        
                    # MCP hívás
                    tcnt += 1
                    yield{"type": "toolcall", "content": f"[{tcnt}/{len(tool_calls_in_answer)}] {tool_call['name']}({tool_call['args']})"} # type: ignore

                    observation = await self.call_mcp_tool( tool_call["name"], tool_call["args"] ) # , progress=lambda msg: websocket.send_json( { "type": "toolcall", "content": f"{msg}" } )
                    
                    t_msg = ToolMessage( content=str(observation), tool_call_id=tool_call["id"] )
                    await self.session_data.saveMessage( t_msg )
                    self.messages.append( t_msg )
                    #yield { "type": "toolcall", "content": t_msg.content }

                    if toolCalls > self.max_tool_calls: 
                        yield {"type": "toolcall", "content": "**Túl sok eszköz hívás egy kérdésre.**"}
                        yield {"type": "done" }
                        break

                yield { "type": "done" }

            if len( tool_calls_in_answer ) == 0:
                break

            


    async def Question( self, question: str ):
        if self.getLLM() is None:
            raise Exception( "LLM not configured" )
        
        if question.strip() != "":
            message = HumanMessage( content=question )
            await self.session_data.saveMessage( message )
            self.messages.append( message )

        async for res in self.Continue():
            yield res
