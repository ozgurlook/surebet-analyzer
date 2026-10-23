# Ücretsiz VM kurulumu ve yayın

Bu kurulum tek bir Oracle Cloud Always Free ARM VM üzerinde Docker Compose ile
FastAPI, veri toplayıcı, Telegram bildirimleri, React PWA, Caddy HTTPS ve kalıcı
SQLite veritabanını çalıştırır. `compose.yaml` SQLite dosyasını `surebet_data`
adlı Docker volume içinde tutar; kod güncellemeleri bu volume'ü silmez. Her zaman
yedek alın: VM veya volume silinirse veriler kaybolur.

Uygulamanın 8000 portu dışarı açılmaz. Caddy istemci IP bilgisini proxy
başlığına yazar; Uvicorn bu başlığı yalnızca iç Docker ağından aldığı için
oturum açma hız sınırlaması istemci IP'siyle çalışır. Uygulama portunu doğrudan
internete açmayın.

## 1. VM ve alan adı

1. Oracle Cloud'da **Always Free eligible**, Ubuntu tabanlı Ampere A1 VM açın.
   Ücretsiz sınırı aşmamak için en fazla 2 OCPU ve 12 GB RAM kullanın. Oluşturma
   sırasında home region ve ücretsiz blok depolama sınırlarını kontrol edin.
2. VM'ye sabit genel IP bağlayın. Alan adınızın A kaydını bu IP'ye yönlendirin.
   Caddy'nin sertifika alabilmesi için DNS çözülmeli, VM güvenlik listesinde ve
   işletim sistemi güvenlik duvarında TCP 80 ile 443 açık olmalı. SSH için 22'yi
   yalnızca kendi IP adresinizden açın.
3. VM'ye Docker Engine, Docker Compose plugin ve `git` kurun. Kullanıcınızı
   `docker` grubuna ekleyip yeniden oturum açın. Bu gruptaki kullanıcının VM
   üzerinde yönetici yetkisi olduğunu dikkate alın.
4. Kullanıcıya ait dizini hazırlayın ve kodu bu dizine alın:

   ```sh
   sudo mkdir -p /opt/surebet
   sudo chown "$USER:$USER" /opt/surebet
   git clone <depo-adresi> /opt/surebet
   ```

5. `/opt/surebet/.env` dosyasını yalnızca VM üzerinde oluşturun (`chmod 600`).
   `.env.example` dosyasını şablon olarak kullanabilirsiniz. İçine
   `SUREBET_DOMAIN`, güçlü bir `SUREBET_REGISTRATION_CODE`, isteğe bağlı
   `SUREBET_TELEGRAM_BOT_TOKEN` ve kaynak uç noktalarını (bkz. 2. bölüm) koyun.
   `SUREBET_COOKIE_SECURE=true` Compose tarafından atanır. `.env` ve
   veritabanını depoya eklemeyin.

## 2. Kaynak uç noktaları

Depo hiçbir sağlayıcı adresi içermez. Her kaynak bağlantı bilgisini sunucudaki
`.env` dosyasından okur; Compose bu dosyayı `env_file` ile uygulama
kapsayıcısına aktarır. Anahtarlar kaynağa göre `SUREBET_SOURCE_A_` …
`SUREBET_SOURCE_F_` önekiyle başlar:

| Değişken | Açıklama |
|---|---|
| `SUREBET_<KAYNAK>_ENDPOINT` | Zorunlu. `wss://` veya `https://` uç nokta adresi |
| `SUREBET_<KAYNAK>_ORIGIN` | El sıkışmada gönderilen Origin başlığı; boşsa uç noktanın sunucusu kullanılır |
| `SUREBET_<KAYNAK>_PAGE_URL` | Anonim belirteç veren genel sayfa (Kaynak D); boşsa Origin kullanılır |
| `SUREBET_<KAYNAK>_REALM` | WAMP realm; yalnızca WAMP akışı kullanan kaynaklar |
| `SUREBET_<KAYNAK>_OPERATOR_ID` | Sayısal operatör kimliği; WAMP akışı kullanan kaynaklar ve Kaynak A (JSON-RPC site kimliği) |

Uç noktası tanımlanmamış bir kaynak uygulamayı durdurmaz: sağlık sayfasında
`endpoint not configured` hatasıyla başarısız kaynak olarak görünür, diğer
kaynaklar çalışmaya devam eder. Adresler yalnızca `.env` üzerinden değişir;
web arayüzünde veya veritabanında tutulmaz. `.env` değiştikten sonra
`docker compose up -d --wait` ile uygulamayı yeniden başlatın.

Arayüzü hiçbir kaynağa bağlanmadan denemek için yerelde demo modunu kullanın:

```sh
uv run surebet-web serve --demo --db demo.db
```

Demo modu ağ erişimi olmadan deterministik sahte veri üretir ve `demo`
kullanıcısını oluşturur; şifre her başlatmada yeniden üretilip konsola yazılır.
Bu Compose kurulumu demo modunu kullanmaz.

## 3. Yayına alma ve güncelleme

İlk yayın ve sonraki güncellemeler VM üzerinde elle yapılır:

```sh
cd /opt/surebet
git pull
docker compose up --build -d --wait
docker compose ps
```

İlk yayında `git pull` adımı gerekmez. `.env` ve Docker volume güncellemede
korunur. Canlı siteyi alan adınızdan açın.

`.github/workflows/ci.yml` her pull request ve `main`/`master` gönderiminde
backend testlerini, Ruff ve mypy denetimlerini, frontend testlerini ve
derlemesini çalıştırır. Sunucuya dağıtım yapmaz.

## 4. Mevcut PC veritabanını taşıma

Taşıma için yerel uygulamayı durdurun; böylece yedekten sonra yeni veri yazılmaz.
Proje dizininde PowerShell ile SQLite yedekleme API'sini kullanan komutu çalıştırın:

```powershell
uv run python -m surebet.storage.backup --source surebet.db --output surebet-transfer.db
Get-FileHash surebet-transfer.db -Algorithm SHA256
scp .\surebet-transfer.db KULLANICI@SUNUCU:~/surebet-transfer.db
```

`surebet-transfer.db` dosyası mevcutsa komut onu değiştirmeyi reddeder.
`surebet.db-wal` ayrı kopyalanmaz; yedekleme API'si tamamlanmış işlemleri
tek dosyada toplar. PC'deki asıl `surebet.db` yerinde kalır.

VM'de `sha256sum ~/surebet-transfer.db` çıktısını PC'deki hash ile karşılaştırın.
İlk yayından sonra ve canlı uygulamaya kullanıcı kabul etmeden önce VM'de:

```sh
cd /opt/surebet
docker compose stop app caddy
docker compose run --rm --no-deps \
  -v "$HOME/surebet-transfer.db:/transfer.db:ro" \
  app cp /transfer.db /data/surebet.db
docker compose up -d --wait
docker compose ps
```

Bu geri yükleme, volume'deki veritabanını değiştirir; yalnızca ilk taşıma için
uygulayın. Sonraki `docker compose up --build` güncellemeleri veritabanını
korur. Başarılı giriş ve eski ayarların göründüğünü kontrol ettikten sonra PC
yedeğini güvenli yerde saklayın.

## Sınırlar

Always Free kapasitesi bölgede bulunmayabilir ve Oracle ücretsiz kaynak
koşullarını değiştirebilir. Güncelleme sırasında `docker compose up --build`
kısa süreli kesinti yaratabilir.
