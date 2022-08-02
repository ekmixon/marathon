import requests 
import json

def generate_apps():
    apps = [
        {
            'id': f'/app-{i}',
            'cmd': 'sleep 3600',
            'cpus': 0.1,
            'mem': 32,
            'instances': 0,
        }
        for i in range(1000)
    ]

    return {'id': '/', 'groups': [], 'apps': apps}

def main():
    apps = generate_apps()
    r = requests.put("http://localhost:8080/v2/groups?force=true", json=apps)
    print(r.text)
    r.raise_for_status()

if __name__ == "__main__":
    main()
