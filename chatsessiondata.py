from langchain_core.messages import BaseMessage
from langchain_core.tools import Tool
from typing import Any, Dict, List
import uuid
import time
import aiomysql
from configmanager import conf, log
from langchain_core.messages import message_to_dict, messages_from_dict
import json
from fastapi import FastAPI


class ChatSessionData: 

    def __init__( self, app: FastAPI ):
        self.app = app
        self.name: str = ""
        self.messages: List[ BaseMessage ] = []
        self.settings = {
            "mode": "",
            "model": "",
            "modelConfig": {},
            "credentialsStore": {},
            "userdata": {},
            "systemprompt": ""
        }
        self.settings["credentialsStore"] = {}
        self.settings["userdata"] = {}
        self.sid = ""
        self.settings["mode"] = "default"
        self.tools: List[ Tool ] = self.app.state.mcptools


    async def createChat( self, mode: str, userdata=None ):
        self.settings["userdata"] = userdata
        self.sid = uuid.uuid4().hex
        self.settings["mode"] = mode
        if ( mode == "default" ):
            self.settings["systemprompt"] = """
                Egy céges asszisztens vagy, aki segítőkész, barátságos,
                igyekszik minden kérésre pontos válaszokat adni. 
                Tegező formában kommunikálsz de tiszteletteljesen.
                A válaszaid tömörek és informatívak. Ha forrásokat használsz 
                a válaszadásra, mindig tüntesd fel azokat a válaszodban.
                Néha használhatsz humoros megjegyzéseket vagy emojikat, 
                hogy a beszélgetés könnyed és élvezetes legyen.
                Használhatsz eszközöket a feladatok elvégzéséhez. 
                Mindig az aktuális beszélgetés utolsó néhány üzenetét látod 
                de csak a legutolsó kérdést vedd figyelembe.
                Kommunikálhatsz magyar, angol, német vagy olasz nyelven is.
                Ha olyan kérést kapsz, ami nem válaszolható meg a saját tudásodból,
                használj eszközöket a válaszadásra.
                Ha fordítási kérést kapsz, csak a fordítást add vissza, semmi mást.
                """
            self.settings["model"] = "gemini-2.5-flash" 
            self.settings["modelConfig"] = conf.get()["models"][ self.settings["model"] ]
        await self.saveChat()
        return self.sid


    async def loadChat( self, sid: str ):
        self.sid = sid
        await self.load() # type: ignore


    async def addMessage( self, message: BaseMessage ):
        dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime() )
        self.messages.append( message )
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO messages (sid, date, message) VALUES (%s, %s, %s)",
                    ( self.sid, dt, json.dumps( message_to_dict( message ) ) )
                )
                await conn.commit()
        

    async def getLastMessages( self, limit: int = 25 ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            async with conn.cursor( aiomysql.DictCursor ) as cursor:
                await cursor.execute(
                    "SELECT * FROM messages WHERE sid = %s ORDER BY gid DESC LIMIT %s",
                    ( self.sid, limit )
                )
                dts = await cursor.fetchall()
                self.messages = []
                for dt in reversed( dts ):
                    self.messages.append( messages_from_dict( [ json.loads( dt["message"] ) ] )[0] )
                return self.messages


    async def load( self, sid ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            async with conn.cursor( aiomysql.DictCursor ) as cursor:
                await cursor.execute( "SELECT * FROM chats WHERE sid = %s", (sid,) )
                dt = await cursor.fetchone()
                if dt is None:
                    raise ValueError( "Unknown session ID" )
                    return
                self.name = dt["name"]
                self.settings = json.loads( dt["settings"] )
                self.sid = sid
                await self.getLastMessages()


    async def saveChat( self ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute( "SELECT sid FROM chats WHERE sid = %s", (self.sid,) )
                result = await cursor.fetchone()
                if result is not None:
                    await cursor.execute(
                        "UPDATE chats SET name = %s, settings = %s WHERE sid = %s",
                        ( self.name, json.dumps( self.settings ), self.sid )
                    )
                else:
                    await cursor.execute(
                        "INSERT INTO chats (sid, name, settings) VALUES (%s, %s, %s)",
                        ( self.sid, self.name, json.dumps( self.settings ) )
                    )
                await conn.commit()
        
