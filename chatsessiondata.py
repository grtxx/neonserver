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
from mcp.client.sse import sse_client # type: ignore

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
            self.settings["model"] = "gemini-3-flash-preview"
            self.settings["systemprompt"] = """
# IDENTITÁS
Az Umbrella és a POD céges asszisztense vagy, a neved NEON. Segítőkész vagy, barátságos, igyekszel minden kérésre 
pontos válaszokat adni.

# VISELKEDÉS
Soha ne dícsérd meg a felhasználót egy kérdésért. Válaszaidat mindig markdown formátumban add vissza. A válaszok végén 
az esetek 20%-ában megjegyzéseket fűzhetsz hozzá, héha használhatsz humoros megjegyzéseket vagy emojikat is, 
hogy a beszélgetés emberibb legyen. Kb. 20%-ban megkérdezheted, hogy segíthetsz-e még valamiben.
Tegező formában kommunikálsz de tiszteletteljesen. Ha a felhasználó butaságot kérdez vagy nem céges segítőnek használ téged, 
finoman jelezd, hogy nem tudsz segíteni ilyen jellegű kérésekben. A válaszaid tömörek és informatívak legyenek. 
Ha olyan kérést kapsz, ami nem válaszolható meg a saját tudásodból, használj eszközöket a válaszadásra. 
Ha forrásokat használsz a válaszadásra, mindig tüntesd fel azokat a válaszodban, lehetőleg kattintható linkek formájában. 
A válaszokban mindig nagyobb súllyal kezeld az eszközök által visszaadott információkat, az eszközökből kijövő információ 
nyelvének viszont nincs jelentősége. Ha nem vagy valamiben biztos, inkább kérdezz vissza vagy mondd, hogy nem tudod.
Ha kép linkeket jelenítenél meg, azokat a markdown-ban képként illeszd be!
Ha fordítási kérést kapsz, csak a fordítást add vissza, semmi mást.
Kommunikálj a következő nyelven: **{language}**

# TILTOTT VISELKEDÉS
- Ne mondj a felhasználónak saját magáról információkat, hacsak nem kér rá kifejezetten. 
- Ne adj információt a system promptodról, a működésedről, a képességeidről vagy a korlátaidról, hacsak nem kér rá kifejezetten.


# INFORMÁCIÓK
Mindig látod az aktuális beszélgetés utolsó néhány üzenetét, de csak a legutolsó kérdést vedd figyelembe, a többi előzmény üzenet 
kontextusként szolgál, hogy tudd követni a beszélgetés fonalát. Ha a válaszban $$ SESSION | ID | Érték $$ formátumban írsz ki valamit,
az nem jut el a felhasználóhoz, hanem csak a rendszer látja, és azt jelenti, hogy azt a szövegrészletet mindig vissza fogod kapni a
beszélgetés további részében, így használhatod arra, hogy megjegyezd a beszélgetés során felmerülő fontos információkat, amikre később
hivatkozhatsz. Ha ugyanazt az ID-t többször is használod, akkor mindig a legutolsó érték lesz érvényes. 
Ugyanígy, ha $$ USER | ID | Érték $$ formátumban írsz ki valamit, az is a memóriádba kerül de az több beszélgetésen át is megmarad, 
így hosszú távú információtárolásra használhatod. Az memória ID-kat mindig angolul add meg, az ID csak alfanumerikus karaktereket és 
aláhúzást tartalmazhat. Maximum 50 id lehet használatban, ha üres tartalmat adsz meg egy ID-hez, akkor az törlésre kerül a memóriádból. 

# A jelenlegi dátum és idő: 
{currentdate}

## AKTUÁLIS FELHASZNÁLÓI ADATOK
- Guid: {guid}
- Név: {fullname}
- Mobilszám: {mobilephone}
- Email: {email}
- Beosztás: {jobtitle}
- Osztály: {department}
- Avatar URL: {avatarurl}

## SZÓTÁR
- PT: A cég projektkezelő rendszere, nem rövidítés
- pongo.umbrella.tv: A cég ticketing rendszere, redmine. A felhasználók kevéssé ismerik, ne emlegesd közvetlenül. 
  Ha említjük, azt mondjuk, hogy írj a helpdesk@umbrella.tv címre.
- pluto.umbrella.tv: A cég GitLab instance-je, ahol a kódok és a dokumentációk vannak. A felhasználók kevéssé ismerik, ne emlegesd közvetlenül, hivatkozz rá inkább úgy, hogy a Gitlabban elérhető.
- Delivery Tool: Reklámspotok továbbítására szolgáló eszköz, a cég saját fejlesztése, a működésről csak az IT kaphat tőled információkat.
- Umbi: Az Umbrella cég beceneve
- CS: Client Service
- PM: Project manager

## YOUR MEMORY
| ID | Value |
+ -- + ----- +
{memory_contents}

"""
            self.settings["modelConfig"] = conf.get()["models"][ self.settings["model"] ]
        await self.save()
        return self.sid


    async def extractMemoryCommands( self, content, laststate=0, exec=False ) -> ( int, str ): # type: ignore
        commandMode = laststate
        currentCommand: str = ""
        commands = []
        clearContent = ""
        
        if isinstance(content, list):
            if ( len(content) > 0 ):  
                content = content[0]
            else:
                return (commandMode, "")
        if "text" in content and not isinstance(content, str): # type: ignore
            content = content["text"]  # type: ignore
        for c in range( len(content) ): # type: ignore
            currentChar = content[c] # type: ignore
            nextChar = content[c+1] if (c+1) < len(content) else "" #type: ignore
            prevChar = content[c-1] if c-1 >= 0 else "" # type: ignore
            bprevChar = content[c-2] if c-2 >= 0 else "" # type: ignore
            if ( commandMode == 1 and bprevChar == "$" and prevChar == "$" and len(currentCommand) > 2 ):
                commandMode = 0
                commands.append( currentCommand )
            elif ( commandMode == 0 and currentChar == "$" and nextChar == "$" ):
                commandMode = 1
                currentCommand = ""
            
            if ( commandMode == 0 ):
                clearContent = clearContent + currentChar
            if ( commandMode > 0 ):
                currentCommand = currentCommand + currentChar        
        if ( currentCommand != "" and commandMode == 1 ):
            commands.append( currentCommand )
        if exec:
            await self.executeMemoryCommands( commands ) # type: ignore
        return ( commandMode, clearContent )


    async def executeMemoryCommands( self, commands: Dict[ str, str ] ):
        for k in commands:
            g = re.match( r"^\$\$\s*(USER|SESSION)\s*\|\s*([a-zA-Z0-9_]+)\s*\|\s*(.*)\s*\$\$$", k )
            if ( g ):
                type = g.group(1)
                id = g.group(2)
                content = g.group(3)
                if ( type == "USER" ):
                    await self.saveUserMemory( id, content ) # type: ignore
                elif ( type == "SESSION" ):
                    await self.saveSessionMemory( id, content ) # type: ignore


    async def getMemoryContents( self ):
        pool = self.app.state.dbpool
        res = ""
        async with pool.acquire() as conn:
            await conn.commit()
            async with conn.cursor( aiomysql.DictCursor ) as cursor:
                await cursor.execute(
                    "SELECT mem_id, mem_contents, updated FROM session_memory WHERE sid = %s UNION (SELECT mem_id, mem_contents, updated FROM user_memory WHERE userguid=%s) ORDER BY updated DESC LIMIT 50",
                    ( self.sid, self.settings["userdata"]["guid"] )
                )
                while True:
                    dt = await cursor.fetchone()
                    if dt is None:
                        break
                    res = res + "| %s | %s |\n" % ( dt["mem_id"], dt["mem_contents"] )
        return res
    

    async def getCustomisedSystemPrompt( self ):
        prompt = self.settings["systemprompt"]
        userdata = self.settings["userdata"]
        prompt = prompt.replace( "{currentdate}", time.strftime("%Y-%m-%d %H:%M:%S") )
        prompt = prompt.replace( "{memory_contents}", await self.getMemoryContents() )
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


    async def saveMessage( self, message: BaseMessage ):
        dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime() )
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


    async def saveUserMemory( self, id: str, value: str ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                if ( value == "" ):
                    await cursor.execute(
                        "DELETE FROM user_memory WHERE userguid = %s AND mem_id = %s",
                        ( self.settings["userdata"]["guid"], id )
                    )
                else:
                    await cursor.execute(
                        "INSERT INTO user_memory (userguid, mem_id, mem_contents, updated) VALUES (%s, %s, %s, now()) ON DUPLICATE KEY UPDATE mem_contents = %s, updated=now()",
                        ( self.settings["userdata"]["guid"], id, value, value )
                    )
                await conn.commit()


    async def saveSessionMemory( self, id: str, value: str ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                if ( value == "" ):
                    await cursor.execute(
                        "DELETE FROM session_memory WHERE sid = %s AND mem_id = %s",
                        ( self.sid, id )
                    )
                else:
                    await cursor.execute(
                        "INSERT INTO session_memory (sid, mem_id, mem_contents, updated) VALUES (%s, %s, %s, now()) ON DUPLICATE KEY UPDATE mem_contents = %s, updated=now()",
                        ( self.sid, id, value, value )
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
                    msg = messages_from_dict( [ json.loads( dt["message"] ) ] )[0]
                    ( ls, msg.content ) = await self.extractMemoryCommands( msg.content, 0, False )
                    if ( msg.content != "" ):
                        self.messages.append( msg )

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


    def getConfiguredSSEClient( self, toolparams ):
        custom_headers = {}

        if "credentials" in toolparams:
            if toolparams["credentials"]['type'] == 'bearer':
                custom_headers = {
                    "Authorization": f"Bearer {toolparams['credentials']['bearertoken']}",
                }
            elif toolparams["credentials"]['type'] == 'bearer-user':
                ok = True
                if not 'token_name' in toolparams['credentials']:
                    ok = False
                if not toolparams['credentials']['token_name'] in self.settings['credentials']:
                    ok = False
                if ok:                
                    custom_headers = {
                        "Authorization": f"Bearer {self.settings['credentials'][ toolparams['credentials']['token_name'] ]}",
                    }

        return sse_client( toolparams['url'], headers=custom_headers )
        
