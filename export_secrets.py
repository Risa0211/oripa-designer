"""credentials.json を Streamlit Secrets (TOML) 形式で出力"""
import json

with open("credentials.json") as f:
    d = json.load(f)


def escape_toml_string(s: str) -> str:
    """TOML basic string の特殊文字をエスケープ"""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


print('app_password = "ここに決めたパスワードを入れる"')
print()
print("[gcp_service_account]")
for k, v in d.items():
    escaped = escape_toml_string(str(v))
    print(f'{k} = "{escaped}"')
