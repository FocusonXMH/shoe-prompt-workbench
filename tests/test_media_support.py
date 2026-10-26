import unittest
from fastapi import HTTPException
from media_support import read_upload,preview

class Request:
    def __init__(self,kind,chunks):self.headers={'content-type':kind};self.chunks=chunks
    async def stream(self):
        for chunk in self.chunks:yield chunk

class MediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_supported_media_signatures(self):
        for kind,body in [('image/png',b'\x89PNG\r\n\x1a\n'),('image/jpeg',b'\xff\xd8\xff'),('video/mp4',b'\x00\x00\x00\x18ftypisom'),('video/webm',b'\x1aE\xdf\xa3')]:
            actual,data=await read_upload(Request(kind,[body[:2],body[2:]]))
            self.assertEqual(actual,kind);self.assertEqual(data,body)
    async def test_wrong_format_and_oversize_rejected(self):
        for req,status in [(Request('image/svg+xml',[b'<svg/>']),415),(Request('video/mp4',[b'not a video']),415),(Request('image/png',[b'\x89PNG\r\n\x1a\n',b'x'*(10*1024*1024)]),413)]:
            with self.assertRaises(HTTPException) as raised:await read_upload(req)
            self.assertEqual(raised.exception.status_code,status)
    def test_previews_do_not_show_internal_anchor_ids(self):
        self.assertEqual(preview('前\n[[media:12345678-1234-1234-1234-123456789abc]]\n后'),'前\n[附件]\n后')
