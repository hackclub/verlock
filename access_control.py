"""One persistent member/admin allowlist, with a one-time legacy migration."""
import os
from threading import RLock
from state_store import load_state, update_state

ACCESS_FILE = 'access.json'
access_lock = RLock()


def load_access():
    with access_lock:
        people = load_state(ACCESS_FILE, None)
        if people is not None:
            return people
        # Preserve existing access while retiring organizations and ENV-only roles.
        migrated = {}

        def add(slack_id, role, email=None):
            slack_id = slack_id.strip()
            if not slack_id:
                return
            old = migrated.get(slack_id, {})
            migrated[slack_id] = {'slack_id': slack_id, 'role': 'admin' if role == 'admin' or old.get('role') == 'admin' else 'member'}
            if email or old.get('email'):
                migrated[slack_id]['email'] = email or old['email']

        for person in load_state('users.json', []):
            add(person['slack_id'], person.get('role', 'member'), person.get('email'))
        for organization in load_state('organizations.json', []):
            for slack_id in organization.get('users', []):
                add(slack_id, 'member')
            for slack_id in organization.get('admins', []):
                add(slack_id, 'admin')
        for slack_id in os.getenv('ADMIN_USERS', '').split(','):
            add(slack_id, 'admin')
        people = sorted(migrated.values(), key=lambda p: p['slack_id'])
        return update_state(ACCESS_FILE, None, lambda current: people if current is None else current)


def role_for(slack_id):
    return next((p['role'] for p in load_access() if p['slack_id'] == slack_id), None)


def update_access(change):
    load_access()
    return update_state(ACCESS_FILE, [], lambda people: sorted(change(people), key=lambda p: p['slack_id']))
