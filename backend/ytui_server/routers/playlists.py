"""Local playlists endpoints (server-side, shared across devices)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..models import PlaylistIn, PlaylistItemIn, PlaylistItemOut, PlaylistOut

router = APIRouter()


def _to_out(row) -> PlaylistOut:
    return PlaylistOut(
        id=row.id, name=row.name, created_at=row.created_at, count=row.item_count
    )


@router.get("/playlists", response_model=list[PlaylistOut])
async def list_playlists(request: Request) -> list[PlaylistOut]:
    return [_to_out(row) for row in request.app.state.db.list_playlists()]


@router.post("/playlists", response_model=PlaylistOut, status_code=201)
async def create_playlist(body: PlaylistIn, request: Request) -> PlaylistOut:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Empty playlist name")
    playlist_id = request.app.state.db.create_playlist(name)
    if playlist_id is None:
        raise HTTPException(status_code=409, detail="Playlist name already taken")
    return _to_out(request.app.state.db.get_playlist(playlist_id))


@router.patch("/playlists/{playlist_id}", response_model=PlaylistOut)
async def rename_playlist(playlist_id: int, body: PlaylistIn, request: Request) -> PlaylistOut:
    db = request.app.state.db
    if db.get_playlist(playlist_id) is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Empty playlist name")
    if not db.rename_playlist(playlist_id, name):
        raise HTTPException(status_code=409, detail="Playlist name already taken")
    return _to_out(db.get_playlist(playlist_id))


@router.delete("/playlists/{playlist_id}", status_code=204)
async def delete_playlist(playlist_id: int, request: Request) -> None:
    if not request.app.state.db.delete_playlist(playlist_id):
        raise HTTPException(status_code=404, detail="Playlist not found")


@router.get("/playlists/{playlist_id}/items", response_model=list[PlaylistItemOut])
async def playlist_items(playlist_id: int, request: Request) -> list[PlaylistItemOut]:
    db = request.app.state.db
    if db.get_playlist(playlist_id) is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return [
        PlaylistItemOut(position=position, video=video)
        for position, video in db.playlist_items(playlist_id)
    ]


@router.post("/playlists/{playlist_id}/items", status_code=201)
async def add_playlist_item(playlist_id: int, body: PlaylistItemIn, request: Request) -> dict:
    db = request.app.state.db
    if db.get_playlist(playlist_id) is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if not db.add_playlist_item(playlist_id, body.video):
        raise HTTPException(status_code=409, detail="Item already in playlist")
    return {"status": "added"}


@router.delete("/playlists/{playlist_id}/items/{position}", status_code=204)
async def remove_playlist_item(playlist_id: int, position: int, request: Request) -> None:
    db = request.app.state.db
    if db.get_playlist(playlist_id) is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if not db.remove_playlist_item(playlist_id, position):
        raise HTTPException(status_code=404, detail="No item at this position")
