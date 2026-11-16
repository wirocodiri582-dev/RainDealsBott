import requests

BASE_URL = "https://raindeals-api.samarthp2727-rd.workers.dev"
API_KEY = "AK_Xl2R76jtYgZI149TQ9_h6s2cXVGD5Kx5Hd6BtTmQ_nM"

payload = {
    "service_id": "60005",
    "quantity": 1,
    "client_order_id": "shop-order-10001",
    "buyer_info": {
        "email": "samarthp2727@gmail.com"
    }
}

response = requests.post(
    f"{BASE_URL}/api/v1/order",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    json=payload,
    timeout=30,
)
print(response.status_code, response.json())