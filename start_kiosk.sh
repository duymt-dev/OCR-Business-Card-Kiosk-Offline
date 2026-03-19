export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/run/user/1000

while ! curl -s http://127.0.0.1:5000 > /dev/null; do sleep 1; done

# Dọn dẹp tiến trình cũ nếu có kẹt lại để tránh lỗi mở chồng tab
killall -9 chromium-browser chromium 2>/dev/null

# Chạy với --start-fullscreen và --app (app mode không bị dính thanh tab)
/usr/bin/chromium \
  --incognito \
  --start-fullscreen \
  --app=http://127.0.0.1:5000 \
  --noerrdialogs \
  --disable-infobars \
  --password-store=basic \
  --use-fake-ui-for-media-stream \
  --test-type \ 
  --no-first-run \
  --simulate-outdated-no-au \
  --autoplay-policy=no-user-gesture-required \
  --ozone-platform=wayland
