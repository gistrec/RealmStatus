# Изображения для набора эмодзи

Исходные файлы лежат в `source/`.

| Файл | Откуда скопирован |
| --- | --- |
| `minecraft.icns` | `icons/minecraft.icns` из индекса ресурсов установленной Minecraft Java Edition 26.3 |
| `terraria.icns` | `Terraria.app/Contents/Resources/Terraria.icns` из Steam |
| `stardew.png` | Предоставленный файл `Daco_4464954.png` |

`make emoji-prepare` собирает прозрачные PNG 100 × 100 в `generated/png/`. Скины Minecraft берутся из записей SkinsRestorer, а портреты Terraria из истории и кэша tMapOnline. В `generated/` также хранится локальный кэш текстур и журнал уже загруженных изображений.
