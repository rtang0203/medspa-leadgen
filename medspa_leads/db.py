import sqlite3
import datetime
from typing import Dict, Any, List, Optional
from . import config

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create businesses table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS businesses (
        place_id            TEXT PRIMARY KEY,
        name                TEXT NOT NULL,
        metro               TEXT NOT NULL,
        address             TEXT,
        phone               TEXT,
        website_url         TEXT,
        rating              REAL,
        review_count        INTEGER,
        discovered_at       TEXT,

        -- enrichment: website analysis
        site_fetched_at     TEXT,
        site_fetch_status   TEXT,
        site_platform       TEXT,
        has_ssl             INTEGER,
        is_mobile_friendly  INTEGER,

        -- enrichment: booking
        booking_checked_at  TEXT,
        has_online_booking  INTEGER,
        booking_platform    TEXT,

        -- enrichment: social
        social_checked_at   TEXT,
        instagram_url       TEXT,
        facebook_url        TEXT,
        social_last_post    TEXT,
        social_status       TEXT,

        -- enrichment: contact
        owner_name          TEXT,
        email               TEXT,
        email_status        TEXT DEFAULT 'not_attempted',

        -- scoring
        scored_at           TEXT,
        deficiency_score    INTEGER,
        primary_deficiency  TEXT,

        -- hook + workflow
        hook_text           TEXT,
        hook_generated_at   TEXT,
        review_status       TEXT DEFAULT 'new',
        notes               TEXT
    );
    """)
    
    # Create events table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        place_id    TEXT,
        stage       TEXT,
        level       TEXT,
        message     TEXT,
        created_at  TEXT
    );
    """)
    
    conn.commit()
    conn.close()

def log_event(place_id: Optional[str], stage: str, level: str, message: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    now_iso = datetime.datetime.now().isoformat()
    cursor.execute(
        "INSERT INTO events (place_id, stage, level, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (place_id, stage, level, message, now_iso)
    )
    conn.commit()
    conn.close()

def upsert_business(biz: Dict[str, Any]):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure discovered_at is populated
    if "discovered_at" not in biz or not biz["discovered_at"]:
        biz["discovered_at"] = datetime.datetime.now().isoformat()
        
    query = """
    INSERT INTO businesses (
        place_id, name, metro, address, phone, website_url, rating, review_count, discovered_at
    ) VALUES (
        :place_id, :name, :metro, :address, :phone, :website_url, :rating, :review_count, :discovered_at
    ) ON CONFLICT(place_id) DO UPDATE SET
        name = excluded.name,
        metro = excluded.metro,
        address = COALESCE(excluded.address, address),
        phone = COALESCE(excluded.phone, phone),
        website_url = COALESCE(excluded.website_url, website_url),
        rating = COALESCE(excluded.rating, rating),
        review_count = COALESCE(excluded.review_count, review_count)
    """
    
    cursor.execute(query, biz)
    conn.commit()
    conn.close()
    log_event(biz["place_id"], "discover", "info", f"Upserted business: {biz['name']}")

def update_business(place_id: str, updates: Dict[str, Any]):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
    values = list(updates.values())
    values.append(place_id)
    
    query = f"UPDATE businesses SET {set_clause} WHERE place_id = ?"
    cursor.execute(query, values)
    conn.commit()
    conn.close()

def get_businesses_to_enrich(stage: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Determine filter based on enrichment stage
    if stage == "enrich_site":
        cursor.execute("SELECT * FROM businesses WHERE site_fetched_at IS NULL")
    elif stage == "enrich_booking":
        cursor.execute("SELECT * FROM businesses WHERE booking_checked_at IS NULL")
    elif stage == "enrich_social":
        cursor.execute("SELECT * FROM businesses WHERE social_checked_at IS NULL")
    elif stage == "enrich_contact":
        cursor.execute("SELECT * FROM businesses WHERE email_status = 'not_attempted'")
    elif stage == "hooks":
        # Generate hooks for all scored businesses
        cursor.execute("SELECT * FROM businesses WHERE hook_generated_at IS NULL")
    else:
        cursor.execute("SELECT * FROM businesses")
        
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_all_businesses() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM businesses")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
