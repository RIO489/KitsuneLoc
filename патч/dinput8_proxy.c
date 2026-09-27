/*
 * dinput8.dll proxy — патч міжлітерних відступів для Mary Skelter: Nightmares
 * (український переклад).
 *
 * Проблема
 * --------
 * У трьох місцях движок рахує просування каретки «спрощеним» шляхом, коли
 * в текстовому обʼєкті не стоїть біт 2 прапорців (+0x188 / +0x54):
 *
 *     однобайтовий символ -> advance = scale * halfWidthFactor
 *     багатобайтовий      -> advance = scale            <- повна ширина
 *
 * Через це вся кирилиця в таких віджетах (опис пункту меню, що біжить рядком)
 * малюється з ієрогліфічною шириною і розлазиться. `xadv` зі шрифту у цьому
 * режимі не читається взагалі, тож правками .ffu це не лікується.
 *
 * Патч
 * ----
 * Вимикаємо перехід на спрощений шлях — і вимірювання, і вивід ідуть
 * пропорційним шляхом, де advance = xadv / дільник * scale.
 *
 *   0x64c7b6  74 5D                 -> 90 90        (вимірювання ширини)
 *   0x64d77b  0F 84 9A 00 00 00     -> 90 x6        (просування при виводі)
 *   0x64da6b  74 5D                 -> 90 90        (другий шлях виводу)
 *   0x64cd90  74 56                 -> 90 90        (ширина символу в буфері)
 *
 * Адреси наведені для поточної збірки; шукаємо не за адресами, а за
 * сигнатурами, тож патч переживе оновлення гри.
 *
 * Steam DRM
 * ---------
 * Секція .text зашифрована на диску й розшифровується стабом уже після того,
 * як завантажник підтягне наші імпорти. Тому в DllMain патчити рано: там ще
 * шифротекст. Замість цього піднімаємо потік, який чекає на появу сигнатур.
 *
 * Рядки ПК-порту (v3)
 * -------------------
 * Частина тексту (налаштування керування й екрана, діалоги збережень, назви дій)
 * зашита в .rdata exe, і українська (UTF-8, удвічі довша) на місце не влазить.
 * Програма перекладу кладе поруч ua_strings.bin: пари «англійський рядок →
 * переклад». Після розшифрування коду знаходимо кожен англійський рядок у .rdata
 * (за текстом, тож адреси з нової збірки не страшні), кладемо переклад у свою
 * пам'ять і переставляємо на нього всі вказівники. Де саме лежать вказівники,
 * беремо з таблиці релокацій exe (з файлу на диску) — жодних вгадувань.
 *
 * Збірка: build.bat (MSVC, x86). Готову dinput8.dll покласти поруч з
 * MarySkelter.exe. Знімається видаленням цієї dll.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>

/* ---- справжня dinput8.dll ---- */
static HMODULE hRealDInput = NULL;
static FARPROC pDirectInput8Create;
static FARPROC pDllCanUnloadNow;
static FARPROC pDllGetClassObject;
static FARPROC pDllRegisterServer;
static FARPROC pDllUnregisterServer;

__declspec(naked) void __stdcall Proxy_DirectInput8Create(void)  { __asm { jmp [pDirectInput8Create] } }
__declspec(naked) void __stdcall Proxy_DllCanUnloadNow(void)     { __asm { jmp [pDllCanUnloadNow] } }
__declspec(naked) void __stdcall Proxy_DllGetClassObject(void)   { __asm { jmp [pDllGetClassObject] } }
__declspec(naked) void __stdcall Proxy_DllRegisterServer(void)   { __asm { jmp [pDllRegisterServer] } }
__declspec(naked) void __stdcall Proxy_DllUnregisterServer(void) { __asm { jmp [pDllUnregisterServer] } }

/* ---- лог ---- */
static FILE *logFile = NULL;

static void Log(const char *fmt, ...)
{
    va_list args;
    if (!logFile) return;
    va_start(args, fmt);
    vfprintf(logFile, fmt, args);
    va_end(args);
    fflush(logFile);
}

/* ---- що саме патчимо ---- */
typedef struct {
    const char *name;
    BYTE  sig[24];
    int   siglen;
    int   at;        /* зміщення від початку сигнатури до переходу */
    int   nops;      /* скільки байтів замінити на 0x90 */
    int   done;
} PatchDef;

static PatchDef g_patches[] = {
    { "вимірювання ширини",
      { 0xF6,0x83,0x88,0x01,0x00,0x00,0x04, 0x0F,0x57,0xC0, 0xF3,0x0F,0x11,0x45,0x10, 0x74,0x5D },
      17, 15, 2, 0 },
    { "просування при виводі",
      { 0xF6,0x46,0x54,0x04, 0xF3,0x0F,0x11,0x4D,0x0C, 0x0F,0x84,0x9A,0x00,0x00,0x00 },
      15, 9, 6, 0 },
    { "другий шлях виводу",
      { 0xF6,0x83,0x88,0x01,0x00,0x00,0x04, 0x0F,0x57,0xC0, 0xF3,0x0F,0x11,0x45,0x0C, 0x74,0x5D },
      17, 15, 2, 0 },
    /* Головне місце: сюди записується ширина кожного символу в буфер тексту.
       Прапорець лежить не в тому самому регістрі, що вище, тому й сигнатура
       інша: mulss xmm0,[edi+0x184] / movss [esi+0x34],xmm0 / je. */
    { "ширина символу в буфері",
      { 0xF3,0x0F,0x59,0x87,0x84,0x01,0x00,0x00, 0xF3,0x0F,0x11,0x46,0x34, 0x74,0x56 },
      15, 13, 2, 0 },
};
#define NPATCH (sizeof(g_patches) / sizeof(g_patches[0]))

static BYTE *g_text = NULL;
static DWORD g_textSize = 0;

static void FindText(void)
{
    BYTE *base = (BYTE *)GetModuleHandle(NULL);
    DWORD elf = *(DWORD *)(base + 0x3C);
    WORD  ns  = *(WORD *)(base + elf + 6);
    WORD  os  = *(WORD *)(base + elf + 4 + 16);
    BYTE *sec = base + elf + 4 + 20 + os;
    WORD  s;
    for (s = 0; s < ns; s++) {
        if (memcmp(sec + s * 40, ".text", 5) == 0) {
            g_text     = base + *(DWORD *)(sec + s * 40 + 12);
            g_textSize = *(DWORD *)(sec + s * 40 + 8);
            return;
        }
    }
}

/* Застосувати всі ще не застосовані патчі. Повертає, скільки зроблено зараз. */
static int TryPatch(void)
{
    DWORD i;
    size_t k;
    int applied = 0;

    if (!g_text || g_textSize < 64) return 0;

    for (k = 0; k < NPATCH; k++) {
        PatchDef *pd = &g_patches[k];
        BYTE *hit = NULL;
        int   hits = 0;

        if (pd->done) continue;

        for (i = 0; i + (DWORD)pd->siglen < g_textSize; i++) {
            BYTE *p = g_text + i;
            if (p[0] != pd->sig[0]) continue;
            if (memcmp(p, pd->sig, pd->siglen) != 0) continue;
            hits++;
            hit = p;
            if (hits > 1) break;
        }

        if (hits != 1) {
            if (hits > 1) Log("  %s: сигнатура не унікальна (%d) — пропускаю\n", pd->name, hits);
            continue;
        }

        {
            BYTE *t = hit + pd->at;
            DWORD oldProt;
            if (VirtualProtect(t, pd->nops, PAGE_EXECUTE_READWRITE, &oldProt)) {
                int n;
                for (n = 0; n < pd->nops; n++) t[n] = 0x90;
                VirtualProtect(t, pd->nops, oldProt, &oldProt);
                FlushInstructionCache(GetCurrentProcess(), t, pd->nops);
                pd->done = 1;
                applied++;
                Log("  %s: пропатчено за 0x%08X (%d байтів)\n",
                    pd->name, (unsigned)t, pd->nops);
            } else {
                Log("  %s: VirtualProtect не вдався (err=%lu)\n", pd->name, GetLastError());
            }
        }
    }
    return applied;
}

/* ================================================= рядки ПК-порту (v3) */
static char g_dir[MAX_PATH];            /* тека dll (= тека гри), з '\' у кінці */

typedef struct { DWORD from, to; } Swap;

static int SwapCmp(const void *a, const void *b)
{
    DWORD x = ((const Swap *)a)->from, y = ((const Swap *)b)->from;
    return x < y ? -1 : x > y;
}

static BYTE *ReadWhole(const char *path, DWORD *size)
{
    HANDLE f = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    BYTE *buf = NULL;
    DWORD got = 0;
    if (f == INVALID_HANDLE_VALUE) return NULL;
    *size = GetFileSize(f, NULL);
    if (*size != INVALID_FILE_SIZE && *size > 0) {
        buf = (BYTE *)HeapAlloc(GetProcessHeap(), 0, *size);
        if (buf && (!ReadFile(f, buf, *size, &got, NULL) || got != *size)) {
            HeapFree(GetProcessHeap(), 0, buf);
            buf = NULL;
        }
    }
    CloseHandle(f);
    return buf;
}

/* Секція образу exe в пам'яті за назвою. */
static BYTE *FindSection(const char *name, DWORD *size)
{
    BYTE *base = (BYTE *)GetModuleHandle(NULL);
    DWORD elf = *(DWORD *)(base + 0x3C);
    WORD  ns  = *(WORD *)(base + elf + 6);
    WORD  os  = *(WORD *)(base + elf + 4 + 16);
    BYTE *sec = base + elf + 4 + 20 + os;
    WORD  s;
    for (s = 0; s < ns; s++) {
        if (strncmp((char *)sec + s * 40, name, 8) == 0) {
            *size = *(DWORD *)(sec + s * 40 + 8);
            return base + *(DWORD *)(sec + s * 40 + 12);
        }
    }
    return NULL;
}

/* Таблиця релокацій з файлу exe: [RVA слота з абсолютною адресою]. */
static DWORD *ReadRelocs(DWORD *count)
{
    char exe[MAX_PATH];
    DWORD fsize = 0, n = 0, cap = 0, i;
    BYTE *f, *sec, *p, *end;
    DWORD pe, rva = 0, rsize = 0, raw = 0;
    WORD ns, os, s;
    DWORD *out = NULL;

    *count = 0;
    GetModuleFileNameA(NULL, exe, MAX_PATH);
    f = ReadWhole(exe, &fsize);
    if (!f) return NULL;
    pe = *(DWORD *)(f + 0x3C);
    ns = *(WORD *)(f + pe + 6);
    os = *(WORD *)(f + pe + 20);
    /* PE32: DataDirectory[5] (base relocation) на +0x60 + 5*8 від optional header */
    rva   = *(DWORD *)(f + pe + 24 + 0x60 + 5 * 8);
    rsize = *(DWORD *)(f + pe + 24 + 0x60 + 5 * 8 + 4);
    sec = f + pe + 24 + os;
    for (s = 0; s < ns; s++) {
        DWORD va = *(DWORD *)(sec + s * 40 + 12), vs = *(DWORD *)(sec + s * 40 + 8);
        if (rva >= va && rva < va + vs) raw = rva - va + *(DWORD *)(sec + s * 40 + 20);
    }
    if (!rva || !raw || raw + rsize > fsize) { HeapFree(GetProcessHeap(), 0, f); return NULL; }
    p = f + raw;
    end = p + rsize;
    while (p + 8 <= end) {
        DWORD page = *(DWORD *)p, bsize = *(DWORD *)(p + 4);
        if (bsize < 8 || p + bsize > end) break;
        for (i = 8; i + 2 <= bsize; i += 2) {
            WORD e = *(WORD *)(p + i);
            if ((e >> 12) != 3) continue;           /* IMAGE_REL_BASED_HIGHLOW */
            if (n == cap) {
                DWORD *nw;
                cap = cap ? cap * 2 : 4096;
                nw = out ? (DWORD *)HeapReAlloc(GetProcessHeap(), 0, out, cap * 4)
                         : (DWORD *)HeapAlloc(GetProcessHeap(), 0, cap * 4);
                if (!nw) { n = 0; break; }
                out = nw;
            }
            out[n++] = page + (e & 0xFFF);
        }
        p += bsize;
    }
    HeapFree(GetProcessHeap(), 0, f);
    *count = n;
    return out;
}

/* Підготовлена підміна: {адреса англ. рядка -> адреса перекладу} і слоти релокацій. */
static Swap  *g_sw = NULL;
static DWORD  g_nswap = 0;
static DWORD *g_rel = NULL;
static DWORD  g_nrel = 0;

/* ua_strings.bin: 'UAS1', u32 к-сть, далі {u16 довж., англ. байти, u16 довж., переклад}. */
static void PrepareStrings(void)
{
    char path[MAX_PATH];
    DWORD size = 0, cnt, k, rsz = 0, i, strs = 0;
    BYTE *buf, *p, *end, *rdata;

    lstrcpyA(path, g_dir);
    lstrcatA(path, "ua_strings.bin");
    buf = ReadWhole(path, &size);
    if (!buf) { Log("Рядки: ua_strings.bin немає — пропускаю\n"); return; }
    if (size < 8 || memcmp(buf, "UAS1", 4) != 0) {
        Log("Рядки: ua_strings.bin не того формату\n");
        return;
    }
    cnt = *(DWORD *)(buf + 4);
    rdata = FindSection(".rdata", &rsz);
    if (!rdata || !cnt) { Log("Рядки: немає .rdata або записів (%lu)\n", cnt); return; }
    g_sw = (Swap *)HeapAlloc(GetProcessHeap(), 0, (cnt * 4 + 16) * sizeof(Swap));
    if (!g_sw) return;

    p = buf + 8;
    end = buf + size;
    for (k = 0; k < cnt && p + 2 <= end; k++) {
        WORD le = *(WORD *)p, lu;
        BYTE *en = p + 2, *ua, *copy;
        int found = 0;
        if (en + le + 2 > end) break;
        lu = *(WORD *)(en + le);
        ua = en + le + 2;
        if (ua + lu > end) break;
        p = ua + lu;
        copy = (BYTE *)HeapAlloc(GetProcessHeap(), 0, lu + 1);
        if (!copy) continue;
        memcpy(copy, ua, lu);
        copy[lu] = 0;
        /* кожне входження «\0рядок\0» у .rdata (рядки там вирівняні, тож перед ним нуль) */
        for (i = 1; i + le + 1 < rsz; i++) {
            if (rdata[i] != en[0] || rdata[i - 1] != 0 || rdata[i + le] != 0) continue;
            if (memcmp(rdata + i, en, le) != 0) continue;
            if (g_nswap < cnt * 4 + 16) {
                g_sw[g_nswap].from = (DWORD)(rdata + i);
                g_sw[g_nswap].to = (DWORD)copy;
                g_nswap++;
                found++;
            }
        }
        if (found) strs++;
        else Log("  не знайдено: %.60s\n", (char *)en);
    }
    HeapFree(GetProcessHeap(), 0, buf);
    if (!g_nswap) { Log("Рядки: жодного збігу\n"); return; }
    qsort(g_sw, g_nswap, sizeof(Swap), SwapCmp);
    g_rel = ReadRelocs(&g_nrel);
    if (!g_rel) { Log("Рядки: не прочитав таблицю релокацій exe — пропускаю\n"); g_nswap = 0; return; }
    Log("Рядки: знайдено %lu з %lu, слотів релокацій %lu\n", strs, cnt, g_nrel);
}

/* Переставити вказівники: in_text=0 — усе поза .text (дані не зашифровані, можна
   одразу, ще до першого рядка коду гри); in_text=1 — у коді, після розшифрування. */
static void PatchSlots(int in_text)
{
    BYTE *base = (BYTE *)GetModuleHandle(NULL);
    DWORD i, slots = 0;
    if (!g_nswap || !g_rel) return;
    for (i = 0; i < g_nrel; i++) {
        BYTE *at = base + g_rel[i];
        DWORD *slot = (DWORD *)at;
        int text = g_text && at >= g_text && at < g_text + g_textSize;
        Swap key, *hit;
        DWORD old;
        if (text != in_text) continue;
        key.from = *slot;
        hit = (Swap *)bsearch(&key, g_sw, g_nswap, sizeof(Swap), SwapCmp);
        if (!hit) continue;
        if (VirtualProtect(slot, 4, PAGE_EXECUTE_READWRITE, &old)) {
            *slot = hit->to;
            VirtualProtect(slot, 4, old, &old);
            slots++;
        }
    }
    if (in_text) FlushInstructionCache(GetCurrentProcess(), g_text, g_textSize);
    Log("Рядки: вказівників %s переставлено %lu\n", in_text ? "у коді" : "у даних", slots);
}

/*
 * Чекаємо, поки Steam-стаб розшифрує .text. До того там шифротекст і
 * сигнатури не знайдуться. Пробуємо 60 секунд, далі здаємось тихо —
 * гра працює як без патча.
 */
static DWORD WINAPI PatchThread(LPVOID param)
{
    int tries;
    int total = 0;

    (void)param;

    {
        int strings_done = 0;
        for (tries = 0; tries < 12000; tries++) {      /* 60 с по 5 мс */
            total += TryPatch();
            /* знайдена сигнатура = код розшифровано: час підміняти рядки, поки гра
               не встигла скопіювати вказівники на них */
            if (total > 0 && !strings_done) {
                PatchSlots(1);
                strings_done = 1;
            }
            if (total >= (int)NPATCH) {
                Log("Готово: %d/%d патчів, спроба %d\n", total, (int)NPATCH, tries + 1);
                if (logFile) { fclose(logFile); logFile = NULL; }
                return 0;
            }
            Sleep(5);
        }
        if (!strings_done) PatchSlots(1);
    }

    Log("Час вийшов: застосовано %d з %d. Код або не розшифровано, "
        "або змінився у новій версії гри.\n", total, (int)NPATCH);
    if (logFile) { fclose(logFile); logFile = NULL; }
    return 0;
}

BOOL WINAPI DllMain(HMODULE hModule, DWORD reason, LPVOID reserved)
{
    (void)reserved;

    if (reason == DLL_PROCESS_ATTACH) {
        char dllDir[MAX_PATH];
        char logPath[MAX_PATH];
        char sysDir[MAX_PATH];
        char *slash;
        HANDLE th;

        DisableThreadLibraryCalls(hModule);

        GetModuleFileNameA(hModule, dllDir, MAX_PATH);
        slash = strrchr(dllDir, '\\');
        if (slash) *(slash + 1) = '\0';
        lstrcpyA(g_dir, dllDir);
        lstrcpyA(logPath, dllDir);
        lstrcatA(logPath, "ua_patch_log.txt");
        logFile = fopen(logPath, "w");

        Log("=== Mary Skelter UA: патч відступів і рядків, v3 ===\n");

        GetSystemDirectoryA(sysDir, MAX_PATH);
        lstrcatA(sysDir, "\\dinput8.dll");
        hRealDInput = LoadLibraryA(sysDir);
        if (hRealDInput) {
            pDirectInput8Create  = GetProcAddress(hRealDInput, "DirectInput8Create");
            pDllCanUnloadNow     = GetProcAddress(hRealDInput, "DllCanUnloadNow");
            pDllGetClassObject   = GetProcAddress(hRealDInput, "DllGetClassObject");
            pDllRegisterServer   = GetProcAddress(hRealDInput, "DllRegisterServer");
            pDllUnregisterServer = GetProcAddress(hRealDInput, "DllUnregisterServer");
            Log("Системна dinput8: 0x%08X\n", (unsigned)hRealDInput);
        } else {
            Log("НЕ вдалося завантажити системну dinput8 (err=%lu)\n", GetLastError());
        }

        /* дані не зашифровані: вказівники на рядки в .data/.rdata переставляємо тут,
           до того як стаб Steam передасть керування коду гри */
        FindText();
        Log(".text: 0x%08X, розмір 0x%X\n", (unsigned)g_text, g_textSize);
        PrepareStrings();
        PatchSlots(0);

        th = CreateThread(NULL, 0, PatchThread, NULL, 0, NULL);
        if (th) CloseHandle(th);
    }
    return TRUE;
}
