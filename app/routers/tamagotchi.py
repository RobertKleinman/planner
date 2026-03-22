"""
routers/tamagotchi.py — Zeph's Tamagotchi Page
================================================
A pixel-art scene showing Zeph living his day in the tower.
Reads from the inner life system. Auto-refreshes every 2 minutes.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.inner_life import get_current_scene
from app.config import settings

router = APIRouter(tags=["tamagotchi"])


@router.get("/api/zeph/scene")
async def zeph_scene(db: Session = Depends(get_db)):
    """Get current scene state for the tamagotchi view."""
    scene = get_current_scene(db, user_timezone=settings.timezone)
    return JSONResponse(content=scene)


@router.get("/zeph", response_class=HTMLResponse)
async def tamagotchi_page():
    """The tamagotchi page — Zeph living in his tower."""
    return HTMLResponse(content=_TAMAGOTCHI_HTML)


_TAMAGOTCHI_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Zeph</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #0a0e17; display: flex; justify-content: center; align-items: center; min-height: 100vh; font-family: 'Courier New', monospace; overflow: hidden; }

.scene-container {
    position: relative;
    width: 320px;
    height: 240px;
    border: 2px solid #2a2a3a;
    border-radius: 8px;
    overflow: hidden;
    image-rendering: pixelated;
    box-shadow: 0 0 30px rgba(100, 80, 60, 0.3);
}

/* Room backgrounds */
.room {
    position: absolute;
    width: 100%;
    height: 100%;
    transition: background-color 1.5s ease;
}

.room-study { background: #1a1520; }
.room-kitchen { background: #1c1815; }
.room-outside { background: #151c20; }

/* Time-of-day overlay */
.time-overlay {
    position: absolute;
    width: 100%;
    height: 100%;
    pointer-events: none;
    transition: background 2s ease;
    z-index: 1;
}
.time-dawn { background: linear-gradient(180deg, rgba(180,120,60,0.15) 0%, rgba(80,40,20,0.1) 100%); }
.time-day { background: linear-gradient(180deg, rgba(100,140,180,0.08) 0%, transparent 100%); }
.time-dusk { background: linear-gradient(180deg, rgba(200,100,40,0.2) 0%, rgba(60,20,40,0.15) 100%); }
.time-night { background: linear-gradient(180deg, rgba(10,10,30,0.4) 0%, rgba(5,5,20,0.3) 100%); }

/* Furniture — placeholder rectangles */
.furniture {
    position: absolute;
    border-radius: 2px;
}

/* Study furniture */
.desk { width: 80px; height: 30px; background: #3d2b1f; bottom: 40px; left: 30px; }
.bookshelf { width: 25px; height: 70px; background: #2d1f15; bottom: 40px; right: 20px; }
.window-frame { width: 50px; height: 40px; background: #1a2535; border: 2px solid #3a3a4a; top: 20px; left: 50px; border-radius: 3px 3px 0 0; }
.candle { width: 4px; height: 12px; background: #e8d5a0; bottom: 70px; left: 60px; }
.candle-flame {
    width: 6px; height: 8px; background: #ffaa30; border-radius: 50% 50% 50% 50% / 60% 60% 40% 40%;
    bottom: 82px; left: 59px;
    animation: flicker 0.4s ease-in-out infinite alternate;
}
.books-stack { width: 30px; height: 15px; background: #4a2a2a; bottom: 40px; left: 120px; border-radius: 1px; }
.books-stack2 { width: 20px; height: 20px; background: #2a3a4a; bottom: 40px; left: 155px; }

/* Kitchen furniture */
.table { width: 90px; height: 25px; background: #4a3525; bottom: 50px; left: 40px; }
.fireplace { width: 50px; height: 50px; background: #2a1a10; bottom: 40px; right: 30px; border-radius: 4px 4px 0 0; border: 2px solid #3d2b1f; }
.fire {
    width: 30px; height: 20px; background: radial-gradient(ellipse, #ff6600 0%, #cc3300 50%, transparent 100%);
    bottom: 42px; right: 40px;
    animation: firePulse 1.5s ease-in-out infinite alternate;
}
.chair { width: 20px; height: 30px; background: #3d2b1f; bottom: 40px; left: 80px; }

/* Outside elements */
.ground { width: 100%; height: 40px; background: #2a3025; bottom: 0; left: 0; }
.sea { width: 100%; height: 60px; background: linear-gradient(180deg, #1a2535 0%, #152030 100%); bottom: 0; left: 0; }
.cliff { width: 160px; height: 80px; background: #3a3530; bottom: 0; left: 0; border-radius: 0 20px 0 0; }
.tree { width: 15px; height: 40px; background: #2a1a10; bottom: 40px; right: 50px; }
.tree-top { width: 35px; height: 30px; background: #1a3020; bottom: 75px; right: 40px; border-radius: 50%; }
.path { width: 30px; height: 50px; background: #3a3025; bottom: 30px; left: 80px; border-radius: 10px; transform: rotate(-5deg); }

/* Stars (night only) */
.stars { position: absolute; width: 100%; height: 100%; z-index: 0; }
.star {
    position: absolute;
    width: 2px; height: 2px;
    background: #fff;
    border-radius: 50%;
    animation: twinkle 3s ease-in-out infinite;
}

/* Sea waves */
.wave {
    position: absolute;
    width: 40px; height: 3px;
    background: rgba(100, 150, 200, 0.3);
    border-radius: 50%;
    animation: wave 4s ease-in-out infinite;
}

/* Characters — placeholder with labels */
.character {
    position: absolute;
    display: flex;
    flex-direction: column;
    align-items: center;
    z-index: 10;
    transition: left 2s ease, bottom 2s ease;
}

.zeph-sprite {
    width: 20px;
    height: 32px;
    background: #6a5acd;
    border-radius: 6px 6px 2px 2px;
    position: relative;
    /* Sprite slot — replace with <img> later */
    background-image: none; /* url('/sprites/zeph/reading.png') */
    background-size: contain;
}
.zeph-sprite::before {
    content: '';
    position: absolute;
    width: 12px; height: 12px;
    background: #dfc8a0;
    border-radius: 50%;
    top: -8px; left: 4px;
}
.zeph-sprite::after {
    content: '';
    position: absolute;
    width: 16px; height: 5px;
    background: #4a3a8a;
    top: -10px; left: 2px;
    border-radius: 2px;
}

.briar-sprite {
    width: 24px;
    height: 14px;
    background: #8a7a6a;
    border-radius: 8px 10px 3px 3px;
    position: relative;
    background-image: none; /* url('/sprites/briar/sleeping.png') */
    background-size: contain;
}
.briar-sprite::before {
    content: '';
    position: absolute;
    width: 6px; height: 8px;
    background: #7a6a5a;
    border-radius: 50% 50% 0 0;
    top: -5px; left: 2px;
}
/* Tail */
.briar-sprite::after {
    content: '';
    position: absolute;
    width: 10px; height: 3px;
    background: #8a7a6a;
    border-radius: 3px;
    top: 2px; right: -8px;
    transform-origin: left center;
    animation: tailWag 1s ease-in-out infinite;
}

.char-label {
    font-size: 7px;
    color: rgba(200, 200, 220, 0.6);
    margin-top: 2px;
    letter-spacing: 0.5px;
}

/* Activity indicators */
.activity-indicator {
    position: absolute;
    font-size: 10px;
    top: -18px;
    animation: floatUp 2s ease-in-out infinite;
}

/* Animations */
@keyframes flicker {
    0% { opacity: 0.8; transform: scale(1); }
    50% { opacity: 1; transform: scale(1.1) translateY(-1px); }
    100% { opacity: 0.9; transform: scale(0.95); }
}

@keyframes firePulse {
    0% { opacity: 0.7; transform: scale(1); }
    100% { opacity: 1; transform: scale(1.1); }
}

@keyframes bob {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-2px); }
}

@keyframes breathe {
    0%, 100% { transform: scaleX(1); }
    50% { transform: scaleX(1.05); }
}

@keyframes tailWag {
    0%, 100% { transform: rotate(0deg); }
    25% { transform: rotate(15deg); }
    75% { transform: rotate(-10deg); }
}

@keyframes twinkle {
    0%, 100% { opacity: 0.3; }
    50% { opacity: 1; }
}

@keyframes wave {
    0%, 100% { transform: translateX(0); opacity: 0.3; }
    50% { transform: translateX(10px); opacity: 0.6; }
}

@keyframes floatUp {
    0%, 100% { transform: translateY(0); opacity: 0.8; }
    50% { transform: translateY(-3px); opacity: 1; }
}

@keyframes zzz {
    0% { transform: translateY(0) translateX(0); opacity: 1; }
    100% { transform: translateY(-15px) translateX(5px); opacity: 0; }
}

.sleeping-z {
    position: absolute;
    font-size: 8px;
    color: rgba(150, 150, 200, 0.7);
    animation: zzz 2s ease-out infinite;
}
.sleeping-z:nth-child(2) { animation-delay: 0.7s; font-size: 6px; }
.sleeping-z:nth-child(3) { animation-delay: 1.4s; font-size: 10px; }

/* Activity-specific animations */
.zeph-reading .zeph-sprite { animation: bob 3s ease-in-out infinite; }
.zeph-sleeping .zeph-sprite { animation: breathe 4s ease-in-out infinite; opacity: 0.7; }
.zeph-magic .zeph-sprite { animation: bob 1.5s ease-in-out infinite; }
.zeph-walking .zeph-sprite { animation: bob 0.6s ease-in-out infinite; }
.zeph-eating .zeph-sprite { animation: bob 2s ease-in-out infinite; }
.zeph-writing .zeph-sprite { animation: bob 4s ease-in-out infinite; }
.zeph-sitting .zeph-sprite { animation: breathe 5s ease-in-out infinite; }

.briar-sleeping .briar-sprite { animation: breathe 4s ease-in-out infinite; }
.briar-sitting .briar-sprite { animation: breathe 5s ease-in-out infinite; }
.briar-following .briar-sprite { animation: bob 0.8s ease-in-out infinite; }
.briar-eating .briar-sprite { animation: bob 1.5s ease-in-out infinite; }

/* When real sprites are loaded, hide CSS placeholder shapes */
.has-sprite { background-color: transparent !important; }
.has-sprite::before, .has-sprite::after { display: none !important; }
#furniture.hidden { display: none; }

/* Info bar */
.info-bar {
    position: absolute;
    bottom: 0;
    width: 100%;
    background: rgba(10, 10, 20, 0.85);
    padding: 4px 8px;
    font-size: 8px;
    color: #8888aa;
    z-index: 20;
    border-top: 1px solid #2a2a3a;
}
.flavor-text {
    color: #aaaacc;
    font-style: italic;
    font-size: 7px;
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* Floor */
.floor {
    position: absolute;
    bottom: 18px;
    width: 100%;
    height: 2px;
    background: #2a2a3a;
}
</style>
</head>
<body>

<div class="scene-container" id="scene">
    <div class="room" id="room"></div>
    <div class="time-overlay" id="timeOverlay"></div>
    <div id="furniture"></div>
    <div id="stars"></div>

    <div class="character" id="zeph">
        <div id="zephIndicator" class="activity-indicator"></div>
        <div class="zeph-sprite"></div>
    </div>

    <div class="character" id="briar">
        <div class="briar-sprite"></div>
        <div class="char-label">Briar</div>
    </div>

    <div class="info-bar">
        <span id="infoText"></span>
        <div class="flavor-text" id="flavorText"></div>
    </div>
</div>

<script>
const ACTIVITY_ICONS = {
    reading: '📖', writing: '✒️', magic: '✨', eating: '🍲',
    sleeping: '💤', walking: '🚶', sitting: '🌙'
};

const ROOM_FURNITURE = {
    study: `
        <div class="furniture window-frame"></div>
        <div class="furniture desk"></div>
        <div class="furniture candle"></div>
        <div class="furniture candle-flame"></div>
        <div class="furniture bookshelf"></div>
        <div class="furniture books-stack"></div>
        <div class="furniture books-stack2"></div>
        <div class="floor"></div>
    `,
    kitchen: `
        <div class="furniture table"></div>
        <div class="furniture chair"></div>
        <div class="furniture fireplace"></div>
        <div class="furniture fire"></div>
        <div class="floor"></div>
    `,
    outside: `
        <div class="furniture sea"></div>
        <div class="furniture cliff"></div>
        <div class="furniture path"></div>
        <div class="furniture tree"></div>
        <div class="furniture tree-top"></div>
        <div class="furniture wave" style="bottom:25px;left:180px"></div>
        <div class="furniture wave" style="bottom:20px;left:220px;animation-delay:1s"></div>
        <div class="furniture wave" style="bottom:15px;left:200px;animation-delay:2s"></div>
    `
};

// Zeph positions per room
const ZEPH_POS = {
    study:   { reading: {l:38,b:42}, writing: {l:35,b:42}, magic: {l:140,b:42}, sleeping: {l:180,b:30}, sitting: {l:120,b:42}, eating: {l:50,b:42}, walking: {l:100,b:42} },
    kitchen: { eating: {l:55,b:52}, reading: {l:90,b:52}, sitting: {l:85,b:52}, sleeping: {l:180,b:30}, writing: {l:55,b:52}, magic: {l:140,b:52}, walking: {l:100,b:52} },
    outside: { walking: {l:100,b:42}, sitting: {l:90,b:42}, reading: {l:85,b:50}, magic: {l:60,b:50}, sleeping: {l:80,b:35}, eating: {l:70,b:42}, writing: {l:85,b:50} },
};

const BRIAR_POS = {
    study:   { sleeping: {l:150,b:28}, sitting: {l:120,b:28}, following: {l:80,b:28} },
    kitchen: { sleeping: {l:140,b:38}, sitting: {l:100,b:38}, following: {l:70,b:38}, eating: {l:120,b:38} },
    outside: { sleeping: {l:130,b:35}, sitting: {l:120,b:35}, following: {l:140,b:35} },
};

function generateStars() {
    const container = document.getElementById('stars');
    container.innerHTML = '';
    for (let i = 0; i < 20; i++) {
        const star = document.createElement('div');
        star.className = 'star';
        star.style.left = Math.random() * 100 + '%';
        star.style.top = Math.random() * 50 + '%';
        star.style.animationDelay = (Math.random() * 3) + 's';
        star.style.width = (Math.random() > 0.7 ? 3 : 2) + 'px';
        star.style.height = star.style.width;
        container.appendChild(star);
    }
}

// Sprite image cache — tracks which PNGs exist
const _spriteCache = {};
function spriteUrl(path) { return '/static/sprites/' + path + '.png'; }
function trySprite(el, path, fallbackClass) {
    const url = spriteUrl(path);
    if (_spriteCache[path] === true) {
        el.style.backgroundImage = 'url(' + url + ')';
        el.style.backgroundSize = 'contain';
        el.style.backgroundRepeat = 'no-repeat';
        el.style.backgroundPosition = 'center';
        el.classList.add('has-sprite');
    } else if (_spriteCache[path] === false) {
        // Known missing, keep CSS fallback
    } else {
        // Unknown — probe it
        const img = new Image();
        img.onload = function() {
            _spriteCache[path] = true;
            el.style.backgroundImage = 'url(' + url + ')';
            el.style.backgroundSize = 'contain';
            el.style.backgroundRepeat = 'no-repeat';
            el.style.backgroundPosition = 'center';
            el.classList.add('has-sprite');
        };
        img.onerror = function() { _spriteCache[path] = false; };
        img.src = url;
    }
}

function renderScene(data) {
    const room = document.getElementById('room');
    const timeOverlay = document.getElementById('timeOverlay');
    const furniture = document.getElementById('furniture');
    const zeph = document.getElementById('zeph');
    const briar = document.getElementById('briar');
    const indicator = document.getElementById('zephIndicator');
    const info = document.getElementById('infoText');
    const flavor = document.getElementById('flavorText');
    const stars = document.getElementById('stars');

    // Room — try sprite background, fall back to CSS color + furniture
    room.className = 'room room-' + data.room;
    const roomKey = data.room === 'outside' ? 'rooms/outside-' + (data.time_of_day === 'night' ? 'night' : 'day') : 'rooms/' + data.room;

    // Check if room sprite exists — hide furniture if it does
    const roomUrl = spriteUrl(roomKey);
    if (_spriteCache[roomKey] === true) {
        room.style.backgroundImage = 'url(' + roomUrl + ')';
        room.style.backgroundSize = 'cover';
        furniture.className = 'hidden';
    } else if (_spriteCache[roomKey] === false) {
        room.style.backgroundImage = '';
        furniture.className = '';
        furniture.innerHTML = ROOM_FURNITURE[data.room] || '';
    } else {
        const rimg = new Image();
        rimg.onload = function() {
            _spriteCache[roomKey] = true;
            room.style.backgroundImage = 'url(' + roomUrl + ')';
            room.style.backgroundSize = 'cover';
            furniture.className = 'hidden';
        };
        rimg.onerror = function() {
            _spriteCache[roomKey] = false;
            furniture.innerHTML = ROOM_FURNITURE[data.room] || '';
        };
        rimg.src = roomUrl;
        // Show furniture as fallback while probing
        furniture.innerHTML = ROOM_FURNITURE[data.room] || '';
    }

    timeOverlay.className = 'time-overlay time-' + data.time_of_day;

    // Stars only at night/dusk
    if (data.time_of_day === 'night' || data.time_of_day === 'dusk') {
        if (!stars.children.length) generateStars();
        stars.style.display = 'block';
    } else {
        stars.style.display = 'none';
    }

    // Zeph position & animation
    const zp = (ZEPH_POS[data.room] || {})[data.zeph_activity] || {l:100,b:42};
    zeph.className = 'character zeph-' + data.zeph_activity;
    zeph.style.left = zp.l + 'px';
    zeph.style.bottom = zp.b + 'px';
    trySprite(zeph.querySelector('.zeph-sprite'), 'zeph/' + data.zeph_activity);

    // Activity indicator
    if (data.zeph_activity === 'sleeping') {
        indicator.innerHTML = '<span class="sleeping-z">z</span><span class="sleeping-z">z</span><span class="sleeping-z">z</span>';
    } else {
        indicator.textContent = ACTIVITY_ICONS[data.zeph_activity] || '';
    }

    // Briar position & animation
    const bp = (BRIAR_POS[data.room] || {})[data.briar_activity] || {l:140,b:28};
    briar.className = 'character briar-' + data.briar_activity;
    briar.style.left = bp.l + 'px';
    briar.style.bottom = bp.b + 'px';
    trySprite(briar.querySelector('.briar-sprite'), 'briar/' + data.briar_activity);

    // Info
    const timeLabels = { dawn: '🌅 Dawn', day: '☀️ Day', dusk: '🌇 Dusk', night: '🌙 Night' };
    const roomLabels = { study: 'Study', kitchen: 'Kitchen', outside: 'Cliff Path' };
    info.textContent = (timeLabels[data.time_of_day] || '') + '  •  ' + (roomLabels[data.room] || data.room) + '  •  ' + data.weather;
    flavor.textContent = data.flavor || '';
}

async function fetchScene() {
    try {
        const res = await fetch('/api/zeph/scene');
        if (res.ok) {
            const data = await res.json();
            renderScene(data);
        }
    } catch (e) {
        console.error('Failed to fetch scene:', e);
    }
}

// Initial load + refresh every 2 minutes
fetchScene();
setInterval(fetchScene, 120000);
</script>
</body>
</html>'''
