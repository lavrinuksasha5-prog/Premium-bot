async def handle_update_proxy(request):
    try:
        data = await request.json()
        acc_id = data.get("account_id")
        new_proxy = data.get("new_proxy")
        
        accounts = load_json(ACCOUNTS_FILE, [])
        acc = next((a for a in accounts if a["id"] == acc_id), None)
        
        if acc:
            ip = new_proxy.split('@')[-1].split(':')[0] if '@' in new_proxy else new_proxy.split(':')[0]
            country = get_ip_country(ip)
            speed = random.randint(30, 150) # Имитация скорости
            
            acc["proxy"] = new_proxy
            acc["proxy_country"] = COUNTRY_NAMES.get(country, "Неизвестно")
            acc["proxy_flag"] = FLAGS.get(country, "🏳️")
            acc["ip"] = ip
            acc["speed"] = speed
            
            save_json(ACCOUNTS_FILE, accounts)
            return web.json_response({
                "status": "ok",
                "proxy_country": acc["proxy_country"],
                "proxy_flag": acc["proxy_flag"],
                "speed": speed
            })
        return web.json_response({"status": "error", "message": "Аккаунт не найден"}, status=404)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)
