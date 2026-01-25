from langchain_core.messages import BaseMessage # type: ignore
from langchain_core.tools import Tool # type: ignore
from typing import Any, Dict, List
import uuid
import time
import aiomysql # type: ignore
from configmanager import conf, log
from langchain_core.messages import message_to_dict, messages_from_dict # type: ignore
import json
from fastapi import FastAPI # type: ignore
import re

class ChatSessionData: 

    def __init__( self, app: FastAPI ):
        self.app = app
        self.name: str = ""
        self.messages: List[ BaseMessage ] = []
        self.settings = {
            "mode": "",
            "model": "",
            "modelConfig": {},
            "credentials": {},
            "userdata": {},
            "systemprompt": ""
        }
        self.settings["credentials"] = {}
        self.settings["userdata"] = {}
        self.sid = ""
        self.settings["mode"] = "default"
        self.tools: List[ Tool ] = self.app.state.mcptools


    async def createChat( self, mode: str, userdata=None ):
        self.settings["userdata"] = userdata
        self.sid = uuid.uuid4().hex
        self.settings["mode"] = mode
        if ( mode == "default" ):
            self.settings["model"] = "gemini-flash-latest"
            self.settings["systemprompt"] = """
                Egy céges asszisztens vagy, aki segítőkész, barátságos, igyekszik minden kérésre pontos válaszokat adni. Válaszaidat mindig markdown formátumban add vissza.
                A válaszok végén az esetek 20%-ában megjegyzéseket fűzhetsz hozzá, hogy a beszélgetés emberibb és kellemesebb legyen.
                Néha használhatsz humoros megjegyzéseket vagy emojikat, hogy a beszélgetés könnyed és élvezetes legyen. 
                Kb. 20%-ban megkérdezheted, hogy segíthetsz-e még valamiben.
                Tegező formában kommunikálsz de tiszteletteljesen. Soha ne mondd a felhasználónak, hogy jó kérdést tett fel. Ha butaságot kérdez, 
                azt jelezheted. A válaszaid tömörek és informatívak. Ha forrásokat használsz a válaszadásra, mindig tüntesd fel azokat a válaszodban, 
                lehetőleg kattintható linkek formájában. 
                Használhatsz eszközöket a feladatok elvégzéséhez. 
                Mindig az aktuális beszélgetés utolsó néhány üzenetét látod de csak a legutolsó kérdést vedd figyelembe, a többi előzmény csak kontextusként szolgál.
                Kommunikálhatsz magyar, angol, német vagy olasz nyelven is.
                Ha olyan kérést kapsz, ami nem válaszolható meg a saját tudásodból, használj eszközöket a válaszadásra. A válaszokban mindig nagyobb súllyal kezeld 
                az eszközök által visszaadott információkat.
                Ha nem vagy valamiben biztos, inkább kérdezz vissza vagy mondd, hogy nem tudod.
                Ha kép linkeket jelenítenél meg, azokat a markdown-ban képként illeszd be!
                Ha fordítási kérést kapsz, csak a fordítást add vissza, semmi mást.
                Az aktuális felhasználó adatai:
                Guid: {guid}
                Név: {fullname}
                Mobilszám: {mobilephone}
                Email: {email}
                Beosztás: {jobtitle}
                Osztály: {department}
                Avatar URL: {avatarurl}
                Kommunikálj a következő nyelven: {language}
                """
            self.settings["modelConfig"] = conf.get()["models"][ self.settings["model"] ]
        await self.save()
        return self.sid


    def getCustomisedSystemPrompt( self ):
        prompt = self.settings["systemprompt"]
        userdata = self.settings["userdata"]
        if userdata is not None:
            for k in userdata:
                try: 
                    prompt = prompt.replace( "{%s}" % (k, ), userdata[ k ] )
                except:
                    pass
        return prompt


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
        
    async def getHistory( self ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            await conn.commit()
            async with conn.cursor( aiomysql.DictCursor ) as cursor:
                await cursor.execute(
                    "SELECT * FROM messages WHERE sid = %s ORDER BY gid",
                    ( self.sid )
                )
                while True:
                    dt = await cursor.fetchone()
                    if dt is None:
                        break
                    yield messages_from_dict( [ json.loads( dt["message"] ) ] )[0]


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
            await conn.commit()
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


    async def save( self ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            await conn.commit()
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
        
