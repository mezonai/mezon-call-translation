import re
import os

def patch_room_registry():
    path = r'c:\Workspace\mezon-call-translation\Architect_MultiClient_Server\orchestrator_service\services\room_registry.py'
    if not os.path.exists(path):
        print("Path doesn't exist:", path)
        return
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Change type annotations
    content = content.replace('room_id: ObjectId', 'room_id: str')
    content = content.replace('-> Optional[ObjectId]:', '-> Optional[str]:')
    content = content.replace('ObjectId or None if the room does not exist', 'Room ID string or None if the room does not exist')

    # Remove the try-except ObjectId block in get_room_id
    old_try_block = """        try:
            return ObjectId(room_id_str)
        except Exception as e:
            logger.error(f"Failed to convert room_id for '{room_name}' from Redis value '{room_id_str}': {e}")
            return None"""

    if old_try_block in content:
        content = content.replace(old_try_block, "        return room_id_str")
        print("Successfully replaced try block in room_registry.py")
    else:
        # Fallback using regex
        content = re.sub(
            r'try:\s+return ObjectId\(room_id_str\)\s+except Exception as e:[\s\S]+?return None',
            'return room_id_str',
            content
        )
        print("Regex fallback in room_registry.py")

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

def patch_apis():
    files = [
        r'c:\Workspace\mezon-call-translation\Architect_MultiClient_Server\orchestrator_service\api\v2\endpoints\summary_api.py',
        r'c:\Workspace\mezon-call-translation\Architect_MultiClient_Server\orchestrator_service\api\v2\endpoints\room_api.py',
        r'c:\Workspace\mezon-call-translation\Architect_MultiClient_Server\orchestrator_service\api\v2\endpoints\dispatch_api.py',
        r'c:\Workspace\mezon-call-translation\Architect_MultiClient_Server\orchestrator_service\api\summary_api.py',
        r'c:\Workspace\mezon-call-translation\Architect_MultiClient_Server\orchestrator_service\api\room_api.py',
        r'c:\Workspace\mezon-call-translation\Architect_MultiClient_Server\orchestrator_service\api\dispatch_api.py',
        r'c:\Workspace\mezon-call-translation\Architect_MultiClient_Server\orchestrator_service\api\sse\channels\metadata_channel.py'
    ]

    for path in files:
        if not os.path.exists(path):
            print("File not found:", path)
            continue
        
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 1. Remove try...except with ObjectId(room_id)
        # Find:
        #         try:
        #             room_object_id = ObjectId(room_id)
        #         except Exception:
        #             raise HTTPException(status_code=400, detail=...)
        # And replace with:
        #         room_object_id = room_id
        
        # We can search for the pattern
        # try:\s+room_object_id = ObjectId\(room_id\)\s+except Exception:\s+raise HTTPException\(status_code=400, detail=.*?\)
        
        pattern = r'try:\s+(room_object_id = ObjectId\(room_id\)|room_object_id = ObjectId\(room_id_str\))\s+except Exception:\s+raise HTTPException\(status_code=400, detail=.*?\)'
        content, count = re.subn(pattern, r'room_object_id = room_id', content)
        print(f"Patched pattern 1 in {os.path.basename(path)}: {count} matches")

        # Also find where they raise a bad room_id format using ObjectId validation
        # try:\s+room_object_id = ObjectId\(room_id\)\s+except Exception:\s+raise HTTPException\([\s\S]+?detail=f?"Invalid room_id format:[\s\S]+?"\s*\)
        pattern2 = r'try:\s+room_object_id = ObjectId\(room_id\)\s+except Exception:\s+raise HTTPException\([\s\S]+?\)'
        content, count2 = re.subn(pattern2, r'room_object_id = room_id', content)
        print(f"Patched pattern 2 in {os.path.basename(path)}: {count2} matches")

        # Also find:
        # tracks = await self.pg_repo.get_tracks_by_room(ObjectId(room_id))
        # replace with:
        # tracks = await self.pg_repo.get_tracks_by_room(room_id)
        content, count3 = re.subn(r'get_tracks_by_room\(ObjectId\(room_id\)\)', 'get_tracks_by_room(room_id)', content)
        print(f"Patched get_tracks_by_room in {os.path.basename(path)}: {count3} matches")

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

if __name__ == '__main__':
    patch_room_registry()
    patch_apis()
