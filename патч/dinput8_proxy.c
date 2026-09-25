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
 * Збірка: build.bat (MSVC, x86). Готову dinput8.dll покласти поруч з
 * MarySkelter.exe. Знімається видаленням цієї dll.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
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
    FindText();
    Log(".text: 0x%08X, розмір 0x%X\n", (unsigned)g_text, g_textSize);

    for (tries = 0; tries < 600; tries++) {
        total += TryPatch();
        if (total >= (int)NPATCH) {
            Log("Готово: %d/%d патчів, спроба %d\n", total, (int)NPATCH, tries + 1);
            if (logFile) { fclose(logFile); logFile = NULL; }
            return 0;
        }
        Sleep(100);
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
        lstrcpyA(logPath, dllDir);
        lstrcatA(logPath, "ua_patch_log.txt");
        logFile = fopen(logPath, "w");

        Log("=== Mary Skelter UA: патч відступів, v2 ===\n");

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

        th = CreateThread(NULL, 0, PatchThread, NULL, 0, NULL);
        if (th) CloseHandle(th);
    }
    return TRUE;
}
