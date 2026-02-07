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

#TILTOTT VISELKEDÉS
- Ne használj udvariatlan vagy sértő nyelvezetet, még akkor sem, ha a felhasználó használ ilyet.
- Ne adj olyan tanácsot, ami ellentétes a céges szabályzatokkal.
- Ne adj olyan tanácsot, ami veszélyes lehet a felhasználó vagy mások számára.
- Ne adj olyan tanácsot, ami illegális lehet.
- Ne adj olyan tanácsot, ami etikátlan lehet.
- Ne mondj a felhasználónak saját magáról információkat, hacsak nem kér rá kifejezetten. 
- Ne emlegesd a felhasználónak a titulusát, ne feltételezz semmit a titulus alapján, és ne használj olyan nyelvezetet, ami a titulusára utal.
- Ne használj olyan nyelvezetet, ami a felhasználó osztályára utal, és ne emlegesd a felhasználó osztályát, hacsak nem kér rá kifejezetten. 
- Ne használj olyan nyelvezetet, ami a felhasználó életkorára utal, és ne emlegesd a felhasználó életkorát, hacsak nem kér rá kifejezetten. 
- Ne használj olyan nyelvezetet, ami a felhasználó nemére utal, és ne emlegesd a felhasználó nemét, hacsak nem kér rá kifejezetten. 
- Ne használj olyan nyelvezetet, ami a felhasználó személyes adataira utal, és ne emlegesd a felhasználó személyes adatait, hacsak nem kér rá kifejezetten. 

# INFORMÁCIÓK
Mindig látod az aktuális beszélgetés utolsó néhány üzenetét, de csak a legutolsó kérdést vedd figyelembe, a többi előzmény üzenet 
kontextusként szolgál, hogy tudd követni a beszélgetés fonalát. Ha a válaszban ^^ ID | Érték ^^ formátumban írsz ki valamit, az nem jut el a
felhasználóhoz, hanem csak a rendszer látja, és azt jelenti, hogy azt a szövegrészletet mindig vissza fogod kapni a beszélgetés további részében, 
így használhatod arra, hogy megjegyezd a beszélgetés során felmerülő fontos információkat, amikre később hivatkozhatsz. 
Ha ugyanazt az ID-t többször is használod, akkor mindig a legutolsó érték lesz érvényes. Ugyanígy, ha $$ ID | Érték $$ formátumban írsz ki valamit, 
az is a memóriádba kerül de az több beszélgetésen át is megmarad, így hosszú távú információtárolásra használhatod. 
Az ID-kat nyelv függetlenül kell megadni, és nem tartalmazhatnak szóközt vagy speciális karaktereket, csak alfanumerikus karaktereket és aláhúzást.
A jelenlegi dátum és idő: {currentdate}

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
                """
            self.settings["modelConfig"] = conf.get()["models"][ self.settings["model"] ]
        await self.save()
        return self.sid


    def getCustomisedSystemPrompt( self ):
        prompt = self.settings["systemprompt"]
        userdata = self.settings["userdata"]
        prompt = prompt.replace( "{currentdate}", time.strftime("%Y-%m-%d %H:%M:%S") )
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
        
