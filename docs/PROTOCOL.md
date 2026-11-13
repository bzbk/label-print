# HPRT M1（汉码 App 3.7.12-cn）打印协议 — 字节级逆向规格

> 目标：在不依赖汉码 App / 汉印官方 SDK 的前提下，自己实现「手机/PC → HPRT M1 便携标签打印机」的打印。
> 对象：BLE 名 `M1-xxxx` 的 HPRT M1（汉印 M1，203dpi，48mm 打印头）。
> 依据：`apk/hanma.apk` 反编译（jadx 1.5.6），所有结论给 `文件:行号`，关键片段已摘录。
> 结论中不确定的地方统一标注 **【待实测验证】**，不要当定论用。

---

## 0. TL;DR（照这个实现）

| 项目 | 结论 |
|---|---|
| 指令语言 | **HPRT ESC/POLI 私有二进制指令集**（GS(0x1D) 指令族 + GS v 帧；不是 TSPL、不是 CPCL、不是 ESC/POS） |
| 是否纯位图 | **是**。App 只发 1bpp 位图（LZO1X 压缩帧），不发 TEXT/BARCODE/QRCODE 字符命令 |
| 关键帧 | `1D 76 30 30 \| wBytes(LE16) \| rows(LE16) \| rawLen(LE32) \| LZO1X-1(raw 1bpp)` |
| 像素 | 1bpp，**1=黑点**，MSB=最左像素，行按 `ceil(w/8)` 字节对齐，**不取反** |
| 灰度 | `avg(R,G,B) <= 128` → 黑（`PrinterDataCore.CreatePrintBitmapData`） |
| 分包 | BLE 通道单包 ≤ **20 字节**（MTU=23，未协商），操作间隔 **100ms**（App 实现值） |
| 加密 | 连接后必须做 `1B 1C "set mm" n m` 握手；收到 `4F 4B`("OK") 后**所有后续写入逐字节 XOR (n+m)** |
| 结束 | 标签纸：`1D 66 C0 03`（= setPollForm(960)）；连续纸：`1B 1B 01 5A 00`（= setPrintFeed(90)） |
| 状态 | 查询 `1B 12 73`，回包前 2 字节小端 int 按位解析（见 §5） |
| 机型参数 | 203dpi / 打印头 384 dot(48mm) / 标签纸宽上限 400 dot(50mm) / 默认标签 40×60mm / 浓度档 2,3,4 |

**最小可用序列（40×60mm 标签，1 份）**：

```
[握手]  1B 1C 73 65 74 20 6D 6D <n> <m>              ; 期望回 4F 4B
[对齐]  1B 61 01                                     ; 居中（可选）
[浓度]  1D 73 65 74 63 4B                            ; GS "setc" 75（可选）
[位图]  1D 76 30 30 28 00 19 00 E8 03 00 00 <LZO..>  ; 例: wBytes=40, rows=25, 压缩长=1000；多帧则重复
[结束]  1D 66 C0 03                                  ; 标签纸 form feed 960
```

现成样例：`samples/m1_label_40x60.bin`（40×60mm 文字+校准条，1411 字节，20 帧）、
`m1_continuous_40x60.bin`（连续纸版本）、对应 `.hex` 逐字节 dump、
`samples/gen_stream.py`（**可直接跑的参考实现**，用 liblzo2 生成 LZO1X-1）。

> 注意：样例文件**不含握手/加密**（握手需交互等回包）。若打印机要求加密，请先发握手并把后续每个字节 XOR (n+m)；
> 样例也不含 `1B 40`——因为 App 对 M1（subcontract=3）就不发。

---

## 1. 为什么是 ESC/POLI 而不是 TSPL（分支判定证据）

App 是多协议通用客户端，`instruct` 字段决定走哪个 PrintManager。M1 的判定链：

1) `assets/devices.json` 中 M1（`model_id: 315`）的机型档案（完整条目见 §6）：

```json
{"app_code":"348293","bluetooth_name":"M1","command_version":"3","creation_dimension":"40*60",
 "dpi":"203","feats":"RightSpace,RightOffset1,M1PrintExcept,M1PrintSpeed,HiddenBattery",
 "is_check":2,"is_compress":1,"is_encipher":1,"is_v1":0,"label_learning":1,"loading_position":"2",
 "model_id":315,"model_name":"M1","paper_type":"1,2","print_density":"2,3,4","print_head_width":"384",
 "print_mode":"5","print_paper_width":"400","printer_type":"1","subcontract_size":3,"tearing_mode":5}
```

2) `PrinterBean.getInstruct()` 把 `print_mode` 的首段映射成指令集（`com/p002prt/print/data/bean/PrinterBean.java:784-810`）：
`"1"→CPCL, "2"→TSPL, "3"→ZPL, "4"→ESC, "5"→ESC_POLI`。**M1 的 `print_mode="5"` → `ESC_POLI`**。

```java
// com/p002prt/print/data/bean/PrinterBean.java:784
public final String getInstruct() {
    if (TextUtils.isEmpty(this.printMode)) return "—";
    String str = (String) CollectionsKt.first(StringsKt.split$default(this.printMode, new String[]{","}, false, 0, 6, (Object) null));
    switch (str.hashCode()) {
        case 49: return !str.equals("1") ? "Other" : PrinterMapping.CPCL;
        case 50: return !str.equals("2") ? "Other" : "TSPL";
        case 51: return !str.equals("3") ? "Other" : PrinterMapping.ZPL;
        case 52: return !str.equals("4") ? "Other" : "ESC";
        case 53: return !str.equals("5") ? "Other" : PrinterMapping.ESC_POLI;
        ...
```

3) `PrintManagerFacade.getPrintManager()` 按 `instruct` 建实例（`.../utils/printer/PrintManagerFacade.java:90-172`）：

```java
// PrintManagerFacade.java:152
case "TSPL":
    ...
    return this.tsplPrinterManager;
case "ESC_POLI":
    if (this.escpoliPrintManager == null) this.escpoliPrintManager = new ESCPOLIPrinterManager();
    return this.escpoliPrintManager;
```

4) `ESCPOLIPrinterManager` 内部有 `deviceInfo.isM1()` 专门分支（`.../printer/ESCPOLIPrinterManager.java:97`），进一步确认 M1 走该类。

> 对比提示：`TSPLPrinterManager`（`SIZE/CLS/BITMAP/PRINT` 文本协议）是给 T210E/B1/HM-T260L 等 `print_mode=2` 的机型用的，
> **不适用于 M1**。如果你在 BLE 上给人灌裸 TSPL 没反应，原因就是这个。

---

## 2. 传输层

### 2.1 连接方式（App 实测走经典蓝牙 SPP，不是 BLE）

`BasePrintManager.connectBluetooth()`（`.../utils/printer/BasePrintManager.java:250-300`）：

```java
// BasePrintManager.java:257
if (deviceInfo.printerVendor == 0 && matchNoEncryptionPrinterByConfig(deviceInfo.name)) {
    deviceInfo.printerVendor = 1; deviceInfo.encryption = 0; ...
} else { ... }
...
HPRTPrinterHelper.INSTANCE.IsBLEType(false);          // :271  ← 关键：强制 SPP
if (deviceInfo.printerVendor == 1) HPRTConst.isShack = false; else HPRTConst.isShack = true;
int iPortOpen = HPRTPrinterHelper.INSTANCE.PortOpen("Bluetooth," + str);   // :281
```

`BTOperator.OpenPort()` 里 `Is_BLE_Type==false` → 经典 RFCOMM 分支（`HPRTAndroidSDK/BTOperator.java:186`）：

```java
this.mmSocket = remoteDevice != null ? remoteDevice.createInsecureRfcommSocketToServiceRecord(MY_UUID) : null;
...
static {
    MY_UUID  = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB");  // SPP
    MY_UUID2 = UUID.fromString("0000ff13-0000-1000-8000-00805F9B34FB");  // 未用于打印
    bluetooth = 1024;
}
```

对镜像 dex 全量字符串搜索：**没有任何 `0000ff00/ff01/ff02/ff03` 字样**（BLE 服务 UUID 全部是运行时发现的）。
BLE 分支只在 `Is_BLE_Type=true` 时启用：`com.bhm.ble.BleManager` + 动态选特征：

```java
// HPRTAndroidSDK/BTOperator.java:260-279  (连接成功后遍历 GATT)
for (BaseNode baseNode : childNode) {
    CharacteristicNode cn = (CharacteristicNode) baseNode;
    if (cn.getEnableWrite() && serviceUUID.length()==0 && characteristicUUID.length()==0) {
        serviceUUID = cn.getServiceUUID(); characteristicUUID = cn.getCharacteristicUUID();     // 第一个可写特征
    }
    if (cn.getEnableRead() && serviceNotifyUUID.length()==0 && characteristicNotifyUUID.length()==0) {
        serviceNotifyUUID = cn.getServiceUUID(); characteristicNotifyUUID = cn.getCharacteristicUUID(); // 第一个可读特征
    }
}
```

这与实测（service `0000ff00`、write `0000ff02`、notify `0000ff01/ff03`）一致：App 若走 BLE 也是"取第一个可写/可读"。

> **【待实测验证】** M1 同时具有 BLE 与 SPP。App 用的是 SPP；我们计划走 BLE。同一字节流在两条通道上是否等价（打印机内部是否共用同一解析器）需要实测确认。
> 由于 `ff02` 是"透传 UART"型特征的可能性很高，先按同一字节流试。

### 2.2 分包 / MTU / 包间隔

BLE 写队列由 `com.bhm.ble` 实现，默认参数（`com/bhm/ble/data/Constants.java`）：

```java
public static final long DEFAULT_OPERATE_INTERVAL = 100;   // 队列操作间隔 100ms
public static final int  DEFAULT_MTU = 23;                 // 未协商 MTU
public static final boolean DEFAULT_AUTO_SET_MTU = false;  // 不主动 requestMtu
```

`BleOptions.Builder` 默认 `mtu = 23`、`operateInterval = 100`（`com/bhm/ble/attribute/BleOptions.java:260,262`）。
写入前逐包校验长度（`com/bhm/ble/request/BleWriteRequest.java` 的 `writeData()`）：

```java
int mtu = bleOptions != null ? bleOptions.getMtu() : 23;
if (bArrValueAt.length > mtu - 3) {
    ... "第%d个数据包长度(%d) + 3大于设定Mtu(%d)" ...
}
```

→ **单包 ≤ 20 字节**，**包间 ≥100ms**（实现建议 20 字节 + 100ms，或 20 字节 + 等 `01 01` ACK）。
写用 `writeCharacteristic`，特征属性检查要求 `PROPERTY_WRITE(0x08)` 或 `PROPERTY_WRITE_NO_RESPONSE(0x04)`；
实测 `ff02` 为 write-without-response → 用无响应写即可。
**【待实测验证】** 更大 MTU（如 requestMtu 247）是否被接受；保守起见按 20 字节。

### 2.3 加密握手（**最重要的隐藏层**）

开关：`HPRTConst.isShack`（`HPRTAndroidSDK/HPRTConst.java:42`）静态初始 `= true`；
App 在连接时按机型覆盖（`BasePrintManager.java:270-278`）：

```java
if (deviceInfo.printerVendor == 1) HPRTConst.isShack = false;   // 无加密机型（is_encipher==0）
else                              HPRTConst.isShack = true;    // M1: is_encipher=1 → 走加密
```

`printerVendor` 的判定：`matchNoEncryptionPrinterByConfig()` → `printerBean.isNoEncryption()` = `isEncipher == 0`
（`.../printer/BasePrintManager.java:172-195`，`.../data/bean/PrinterBean.java:1026`）。**M1 `is_encipher=1` → 加密开启**。

握手本身（`HPRTAndroidSDK/Check.java:10-30`，由 `HPRTPrinterHelper.PortOpen()` 在连接成功后调用）：

```java
public static int ChackHands(int n, int m) {
    HPRTPrinterHelper.cleanRead();
    byte b3 = (byte) n, b4 = (byte) m;
    if (HPRTPrinterHelper.WriteData(new byte[]{27, 28, 115, 101, 116, 32, 109, 109, b3, b4}) == -1) return -1;
    byte[] bArrReadData = HPRTPrinterHelper.ReadData(3);
    ...
    b2 = bArrReadData[0];
    if (b2 == 79 || bArrReadData[1] != 75) {           // 'O' + 'K'
        return (b2 == 78 && bArrReadData[1] == 71) ? 1 : -4;   // 'N''G'
    }
    return 0;
}
```

- 命令：`1B 1C 73 65 74 20 6D 6D <n> <m>`（10 字节，`n/m` 各 1 字节，App 取 `Random().nextInt(100)+1`）。
- 回包：`4F 4B`（`"OK"`）→ 返回 0 → **`setKey(n,m)` 且之后 XOR 加密**；
  `4E 47`（`"NG"`）或超时 → 返回 1/2 → **不加密**（`n=m=0`，XOR 0 等效明文）。

`PortOpen` 尾段（`HPRTAndroidSDK/HPRTPrinterHelper.java:4010-4049`）：

```java
printer5.setIsFirst(true);
if (!HPRTConst.isShack) { isPortOpen = true; return 0; }
printer6.setIsFirst(false);                     // 注意：握手包本身用 n=m=0 → XOR 0 → 明文
int iNextInt  = new Random().nextInt(100) + 1;
int iNextInt2 = new Random().nextInt(100) + 1;
int iChackHands = Check.ChackHands(iNextInt, iNextInt2);
...
if (iChackHands != 0) { ... return 0; }        // NG/无响应：明文继续
printer8.setIsFirst(false);
printer9.setKey(iNextInt, iNextInt2);          // OK：写入密钥
```

加密算法（`HPRTAndroidSDK/BTOperator.java:352-387`）：

```java
public int WriteData(byte[] Data, int intOffset, int intDataLength) {
    if (this.mmOutStream == null) return -1;
    if (HPRTConst.isShack) {
        if (this.isFrist) {                     // 第一条（握手）: isFrist=false 后就不是了
            if (writeData(Data) == -1) return -1;
        } else {
            byte[] bArr = new byte[Data.length];
            for (int i = 0; i < Data.length; i++) {
                bArr[i] = (byte) (Data[i] ^ (this.n + this.m));   // ★ 逐字节 XOR (n+m)
            }
            if (writeData(bArr) == -1) return -1;
        }
    } else { writeData(Data); }
    ...
}
```

要点：
- XOR key 是 **`(byte)(n + m)`**，对**每一个字节**做 XOR（不是链式、不加长度）。
- 握手命令之后**所有**写（含位图数据、走纸命令、状态查询）都要 XOR；读回（通知）数据**不**解 XOR（
  `ReadData` 直接取原始字节，见 `BTOperator.ReadData`/`ReadDataMillisecond`）。
- 若设备回 NG/无响应，则全程明文。
> **【待实测验证】** M1 对 `set mm` 的具体回包（`4F4B` / `4E47` / 无响应）以及加密状态是否跨连接保持（掉电/重连后是否仍要求加密）。
> 建议实现时：先发握手并等 500ms；收到 `4F4B` 就切 XOR，否则明文。

---

## 3. M1 的指令集（ESC/POLI）逐条

### 3.1 命令总表

| 功能 | 字节 | 说明 | 证据 |
|---|---|---|---|
| 初始化 | `1B 40` | ESC @；**M1 `subcontract=3≠0` → App 不发** | ESCPOLIPrinterManager.java:79-81 |
| 对齐 | `1B 61 <n>` | n: 0=左 1=中 2=右 | HPRTPrinterHelper.java:576-609 |
| 浓度 | `1D 73 65 74 63 <d>` | `"setc"`；d=35/55/75/95 | HPRTPrinterHelper.java:2066-2085；ESCPOLIPrinterManager.java:16-30 |
| 纸张类型 | `1D 73 65 74 70 <t>` | `"setp"` + t | HPRTPrinterHelper.java:2087-2099 |
| 标签学习/定位 | `1D 73 65 74 4C` | `"setL"` | HPRTPrinterHelper.java:2806-2818 |
| 位图帧 | `1D 76 30 30 …` | 见 §4 | PrinterDataCore.java:117-142,184-207；HPRTPrinterHelper.java:3227-3257 |
| 走纸/结束(标签) | `1D 66 <lo> <hi>` | `setPollForm(960)` → `1D 66 C0 03` | HPRTPrinterHelper.java:2964-2971；ESCPOLIPrinterManager.java:106 |
| 走纸(连续纸) | `1B 1B 01 <lo> <hi>` | `setPrintFeed(90)` → `1B 1B 01 5A 00` | HPRTPrinterHelper.java:2192-2209；ESCPOLIPrinterManager.java:108 |
| 状态查询 | `1B 12 73` | ESC DC2 's'，回包 ≥3 字节 | HPRTPrinterHelper.java:1933-1952 |
| 版本查询 | `1B 12 56` / `1B 12 64` | type1 / type2 | HPRTPrinterHelper.java:1517-1541 |
| SN 查询 | `1B 12 4E` | ESC DC2 'N' | HPRTPrinterHelper.java:3009 |
| 电量查询 | `1D 67 65 74 76` | GS "getv"（M1 隐藏电量显示 feats=HiddenBattery） | HPRTPrinterHelper.java:2890-2900 |
| 设备参数读 | `1B 1C 26 20 56 31 20 67 65 74 6B 65 79 0D 0A 01 <kLo> <kHi> <len>` | ESC FS "& V1 getkey\r\n" | CommandData.java:40-42 |
| 设备参数写 | `1B 1C 26 20 56 31 20 73 65 74 6B 65 79 0D 0A 01 <kLo> <kHi> <len> <val..>` | ESC FS "& V1 setkey\r\n" | HPRTPrinterHelper.java:1466-1492 |
| 保存参数区 | `1B 1C 26 20 56 31 20 64 6F 20 22 73 61 76 65 5F 70 61 72 61 6D 5F 7A 6F 6E 65 22 0D 0A` | `& V1 do "save_param_zone"` | HPRTPrinterHelper.java:2143-2167 |

注意：**没有** `SIZE`/`GAP`/`DIRECTION`/`DENSITY`/`SPEED`/`CLS`/`TEXT`/`BARCODE`/`QRCODE`/`PRINT` 这些 TSPL 命令。
行结束符概念也不适用（除 `getkey/setkey` 之类参数命令内部带 `\r\n`）。

### 3.2 打印主流程（M1 实际执行顺序）

`ESCPOLIPrinterManager.printBitmap()`（`com/p002prt/print/utils/printer/ESCPOLIPrinterManager.java:75-125`）：

```java
public synchronized int printBitmap(DeviceInfo deviceInfo, Bitmap bitmap, int perCount, int alignment,
                                    boolean isLabel, boolean containImg, boolean keepPrint, int typeA4) {
    if (deviceInfo.subcontract == 0) HPRTPrinterHelper.WriteData(new byte[]{27, 64});   // M1: 3 → 跳过
    ...
    setImageAlignment(alignment);                       // 1B 61 n
    Bitmap bitmapCopy = bitmap.copy(Bitmap.Config.ARGB_8888, false);
    ...
    while (i3 < perCount) {
        ...
        PrinterStatus printerStatus = new PrinterStatus(deviceInfo.statusValue);
        if (deviceInfo.isM1() && printerStatus.isLocateFail()) return -1;   // 定位失败直接放弃
        if (deviceInfo.subcontract > 0) {
            iPrintBitmap = HPRTPrinterHelper.printBitmapPackage(bitmap, 0, (deviceInfo.subcontract / 2) * 1024);
        } else {
            iPrintBitmap = HPRTPrinterHelper.printBitmap(bitmapCopy, 0, mapEscCompressType(deviceInfo.compress), 200);
        }
        if (isLabel) HPRTPrinterHelper.setPollForm(960); else HPRTPrinterHelper.setPrintFeed(90);
        HPRTPrinterHelper.ReadDataMillisecond(1);
        if (iPrintBitmap <= 0) { i = -1; break; }
        i3++;
    }
    ...
}
```

`setImageAlignment()`（同文件 :60-73）：`alignment 0 → SetJustification(1)`、`1 → SetJustification(2)`、其它 `→ 0`。

### 3.3 每个命令的字节细节

浓度（`setPollPrintDensity`，`HPRTPrinterHelper.java:2066-2085`）：

```java
if (printer2.WriteData(new byte[]{BoolPtg.sid, 115, 101, 116, 99, (byte) density}) == -1) { ... }
```

⚠️ **jadx 假象**：反编译里这些常量被显示成 `BoolPtg.sid`，那是反编译器的"常量替换"把字面量 **29** 误标成了
`org.apache.poi.ss.formula.ptg.BoolPtg.sid`（该类 `sid = 29`）。用 `jadx --no-replace-consts` 复核得到真实值 **29 = 0x1D = GS**：
```java
// jadx --no-replace-consts (HPRTPrinterHelper.java 反编译)
if (printer2.WriteData(new byte[]{29, 115, 101, 116, 99, (byte) density}) == -1) { ... }
//                                   ^^ GS(0x1D) 's' 'e' 't' 'c'
```
所以是 `1D 73 65 74 63 <d>`（**GS** 指令族，不是 ESC 0x1B）。
App 档位映射（`ESCPOLIPrinterManager.java:15-28`）：`1→35, 2→55, 3→75, 4→95`；
M1 档案 `print_density="2,3,4"`（可选 中/浓/最深）。

对齐 / 走纸：

```java
// HPRTPrinterHelper.java:598   SetJustification
byte[] bArr = {27, 97, b};                                   // 1B 61 <0|1|2>

// HPRTPrinterHelper.java:2964  setPollForm(feed)
return printer.WriteData(new byte[]{29 /*0x1D=GS, jadx 显示为 BoolPtg.sid*/, 102, (byte)(feed & 255), (byte)((feed >> 8) & 255)}) != -1;
//   → 1D 66 <lo> <hi>      feed=960 → 1D 66 C0 03

// HPRTPrinterHelper.java:2192  setPrintFeed(feed)
byte[] bArr = {(byte)(feed & 255), (byte)((feed >> 8) & 255)};
WriteData(new byte[]{27, 27, 1, bArr[0], bArr[1]});
//   → 1B 1B 01 <lo> <hi>   feed=90 → 1B 1B 01 5A 00
```

> **【待实测验证】** `setPollForm(960)` 的语义。SDK 命名为 "poll form"，参数固定 960 dot（=120mm@203dpi），
> 猜测是"送纸/切分到 960 dot"或"表单长度"。对 40×60mm 标签来说 960 > 480，若真的多送纸会浪费；
> 也可能只是"允许的最大长度"。实测时优先验证：一张标签打完是否停在撕纸位；
> 若多走纸，尝试把 960 换成 `labelHeight(px)` 或改用 `1B 1B 01 <height>`。

纸张类型：`1D 73 65 74 70 <t>`（`HPRTPrinterHelper.java:2087-2099`）。
M1 的"纸张类型"更常用的是参数写方式（key 206 = 0xCE）：标签纸=2、黑标纸=1（见 §7）。

标签学习/定位：`1D 73 65 74 4C`（`HPRTPrinterHelper.java:2806-2818`，名为 `setGapDetectCPCL`，
但 ESCPOLI 的 `setPaperLearn` 调的正是它）——打印前放入标签纸后执行一次，让打印机测标签间距/定位。

---

## 4. 位图格式（核心）

### 4.1 1bpp 打包规则

`ESCPOLIPrinterManager` 调 `printBitmapPackage(bitmap, bitType=0, packageSize)`；
`PrinterDataCore` 在 `HalftoneMode=0` 时走 `CreatePrintBitmapData()`
（`HPRTAndroidSDK/PrinterDataCore.java:596-644`）：

```java
private byte[] CreatePrintBitmapData(Bitmap bmp) {
    int width = bmp.getWidth();
    int height = bmp.getHeight();
    this.PrintDataHeight = height;
    int i = (width % 8 == 0 ? width : ((width / 8) + 1) * 8) / 8;   // 每行字节数 = ceil(w/8)
    this.BitmapWidth = i;
    int i2 = i * height;
    byte[] bArr = new byte[i2];                                     // 全 0 = 白
    ...
    for (int i6 = 0; i6 < i2; i6++) bArr[i6] = 0;
    int i7 = 0, i8 = 0;
    while (i8 < height) {
        int[] iArr2 = new int[width];
        bmp.getPixels(iArr2, 0, width, 0, i8, width, 1);
        int i9 = 0;
        for (int i10 = 0; i10 < width; i10++) {
            i9++;
            int i11 = iArr2[i10];
            if (i9 > 8) { i7++; i9 = 1; }
            if (i11 != -1) {                                        // 非透明
                int i12 = 1 << (8 - i9);                            // MSB = 最左像素
                if (((Color.red(i11) + Color.green(i11) + Color.blue(i11)) / 3 <= 128) {
                    bArr[i7] = (byte) (bArr[i7] | i12);             // ★ 暗 → 置 1（1=黑）
                }
            }
        }
        i8++;
        i7 = this.BitmapWidth * i8;
    }
    return bArr;
}
```

字节级总结：

- 行宽 `bpr = ceil(width/8)`；总长 `bpr * height`；**行间不额外填充**。
- 每个字节内 **bit7 (0x80) = 该行最左像素**，bit0 = 第 8 个像素。
- **1 = 黑点，0 = 白**（与 TSPL 相反！TSPL 分支会额外整体取反，见 §8）。
- 灰度 = `(R+G+B)/3`，`<= 128` 判黑（**不是**加权灰度、**无**抖动）。
  空白/透明像素（ARGB = -1）直接跳过（视为白）。
- 位图宽度建议 = 标签宽度对应点数（40mm @203dpi → 320 dot → bpr=40），
  高度 = 标签长度点数（60mm → 480 行）。横向对齐由 `1B 61 n` 交给打印机。

### 4.2 LZO 帧封装（"分包/Subcontract"）

`PrinterDataCore.lzoCompress(byte[] data, int width, int height)`（同文件 :184-207）再加包头：

```java
private byte[] lzoCompress(byte[] data, int width, int height) {
    byte[] bArr = new byte[data.length + 12];
    bArr[0] = 29;            // 0x1D (jadx 默认显示为 BoolPtg.sid，实值 29)
    bArr[1] = 118;           // 'v'
    bArr[2] = 48;            // '0'
    bArr[3] = 48;            // '0'
    bArr[4] = (byte)(width  % 256); bArr[5] = (byte)(width  / 256);   // 每行字节数 LE16
    bArr[6] = (byte)(height % 256); bArr[7] = (byte)(height / 256);   // 行数       LE16
    byte[] bArrIntTo4Bytes = intTo4Bytes(data.length);               // LZO 压缩后长度 LE32
    for (int i = 0; i < 4; i++) bArr[i + 8] = bArrIntTo4Bytes[i];
    for (int i2 = 0; i2 < data.length; i2++) bArr[i2 + 12] = data[i2];
    return bArr;
}
```

帧格式（小端，`1D 76 30 30` = `GS v 0 0`，HPRT 在 ESC/POS 的 GS v 0 之后接自家扩展头）：

```
偏移 0  1D 76 30 30          magic: 0x1D(GS), 'v', '0', '0'
偏移 4  wBytes  LE16         = ceil(bitmapWidth/8)      （"每行多少字节"）
偏移 6  rows    LE16         = 该帧包含多少行
偏移 8  lzoLen  LE32         = LZO 压缩后的字节数
偏移 12 LZO1X-1(raw)         raw = rows*wBytes 字节的 1bpp 数据
```

- LZO 算法：**LZO1X-1**（`LZO_Compress/LZOCompress.java` 是 `native lzoCompressData(in,inLen,out,outLen,wrkmem)`，
  `System.loadLibrary("LZO")`；调用时 `wrkmem = new byte[64000]` = `LZO1X_1_MEM_COMPRESS`）。
  可用标准 `lzo1x_1_compress()` 生成（`samples/gen_stream.py` 用 `liblzo2.so` 验证过可生成同构数据）。
- 数据是"1bpp 原始字节流"直接 LZO，**不含** RLE/行差分等其他压缩。

### 4.3 分块（subcontract）规则

`HPRTPrinterHelper.printBitmapPackage()`（`HPRTAndroidSDK/HPRTPrinterHelper.java:3227-3257`）：

```java
public final int printBitmapPackage(Bitmap bitmap, int bitType, int packageSize) throws Exception {
    int width = bitmap.getWidth();
    int i = width % 8, i2 = width / 8;
    if (i != 0) i2++;
    bitmap.getHeight();
    PrinterDataCore printerDataCore = new PrinterDataCore();
    printerDataCore.HalftoneMode = (byte) bitType;          // M1: 0
    printerDataCore.CompressMode = (byte) 4;                // 4 = 返回原始 1bpp（不压缩）
    byte[] bArrPrintDataFormat = printerDataCore.PrintDataFormat(bitmap, 200);
    if (bArrPrintDataFormat.length > packageSize) {
        List<byte[]> list = Tools.addBytesToList(bArrPrintDataFormat, (packageSize / i2) * i2);  // 整行切块
        for (int i3 = 0; i3 < list.size(); i3++) {
            if (WriteData(printerDataCore.SubcontractingLzo(list.get(i3), i2, list.get(i3).length / i2)) == -1) { ... }
        }
        setConnectState(0);
    } else {
        PrintBitmap(bitmap, b, (byte) 0, 200, true);        // 单帧 + 压缩
    }
    return 1;
}
```

- M1：`subcontract_size = 3` → `packageSize = (3/2)*1024 = 1024` 字节。
- raw 总长 > 1024 → 按"整行"切：每块 `chunk = (1024 / bpr) * bpr` 字节（40×60mm: bpr=40 → 每块 1000B = 25 行，
  共 `ceil(480/25)=20` 帧，最后一帧 5 行 200B）。
- raw 总长 ≤ 1024 → 单帧（`PrintBitmap` → `CreateBitmapPrintDatas(..., isCompress=true)` → `CompressMode=3`
  → `lzoCompress(lzoCompressData(raw))`，同样包头；写时再按 10000 字节切 WriteData）。
- **每帧是一段连续行**，打印机按顺序拼接；总高度=各帧 rows 之和。没有"总高度"字段。

### 4.4 完整拼接顺序（伪代码）

```python
# M1 subcontract=3 → App 不发 1B 40 初始化，直接开始
send( 0x1B,0x61,align )                     # 对齐
send( 0x1D,0x73,0x65,0x74,0x63,density )    # 浓度（可选）
raw = pack_1bpp(canvas)                     # 见 4.1
bpr = ceil(w/8)
if len(raw) > 1024:
    chunk = (1024 // bpr) * bpr
    for part in chunks(raw, chunk):
        send( 0x1D,0x76,0x30,0x30, LE16(bpr), LE16(len(part)//bpr), LE32(len(lzo(part))), lzo(part) )
else:
    send( frame(raw, bpr, h) )
send( 0x1D,0x66, 960 & 0xFF, 960 >> 8 )     # 标签纸: 1D 66 C0 03
# 每份重复"位图帧 + 走纸"；多份 = 重复整段
```

---

## 5. 状态回包（通知特征）

查询命令：`1B 12 73`（`HPRTPrinterHelper.java:1933-1952`）：

```java
public final byte[] getPrintStatus(int count) throws Exception {
    if (count == 0) return null;
    printer.setConnectState(1);
    if (printer2.WriteData(ExtKt.hexStringToBytes("1B 12 73")) == -1) return null;
    byte[] bArr = ReadDataMillisecond(3000);
    if (bArr.length <= 7) bArr = ReadDataMillisecond(3000);      // 太短则再等一轮
    return bArr.length == 0 ? getPrintStatus(count - 1) : bArr;
}
```

解析（`DeviceService.readPrinterStatus$lambda$23`，`.../ui/service/DeviceService.java:2695-2710`）：

```java
byte[] printStatus = HPRTPrinterHelper.INSTANCE.getPrintStatus(1);
if (printStatus != null && printStatus.length > 2) {
    byte[] bArr = new byte[2];
    System.arraycopy(printStatus, 0, bArr, 0, 2);
    PrinterStatus printerStatus = new PrinterStatus(ByteUtils.INSTANCE.bytes2ToInt_l(bArr, 0));  // 前 2 字节小端
    EventBus.getDefault().postSticky(printerStatus);
}
```

位定义（`com/p002prt/print/data/bean/PrinterStatus.java:194-203`）：

```java
public PrinterStatus(int i) {
    this.value = i;
    this.noPaper    = (i & 1)  == 1;
    this.heightTemp = (i & 2)  == 2;
    this.lowTemp    = (i & 4)  == 4;
    this.lowBattery = (i & 8)  == 8;
    this.openCover  = (i & 16) == 16;
    this.lowVoltage = (i & 32) == 32;
    this.locateFail = (i & 64) == 64;
}
```

| bit | 含义 |
|---|---|
| 0 (0x0001) | 缺纸 |
| 1 (0x0002) | 打印头过热 |
| 2 (0x0004) | 温度过低 |
| 3 (0x0008) | 低电量 |
| 4 (0x0010) | 开盖 |
| 5 (0x0020) | 低电压 |
| 6 (0x0040) | 定位失败（标签间隙/黑标识别失败；M1 打印前会检查此位） |
| 其它 | 未使用 |

App 轮询：打印过程中循环 `getPrintStatus` + `Thread.sleep(100)`（`DeviceService.java` 状态线程），
每次打印成功后也会查一次。M1 不支持的位（如电量，`feats=HiddenBattery`）UI 隐藏。

> 与你在 `ff03` 上看到的 `01 04` / `02 aa 00` / `01 01`：这些是**固件/传输层的私有帧**，Java 侧没有解析分支
> （`DataFilter.java` 只认 `pooli_sta=`、`rtsts`、`status:`、`nfc_uid` 等 MT/POOLI 机型关键字）。
> **【待实测验证】** `01 01` 是否为"写包 ACK"；若是，建议发送端每 20 字节等待该 ACK（比固定 100ms 更稳）。

---

## 6. M1 机型参数（全部来自 App 资源）

`assets/devices.json`，`model_id=315`（该文件是云端机型档案的本地快照，共 391 条）：

| 字段 | 值 | 含义 |
|---|---|---|
| `model_name` / `bluetooth_name` | `M1` / `M1` | 机型名、BLE/SPP 广播名（完整为 `M1-c753` 形态） |
| `dpi` | `"203"` | 203 dpi ≈ 8 dot/mm |
| `print_head_width` | `"384"` | 打印头 384 dot = **48 mm**（最大可打印宽度） |
| `print_paper_width` | `"400"` | 支持纸宽上限 400 dot = **50 mm** |
| `creation_dimension` | `"40*60"` | 默认新建标签尺寸 40×60 mm |
| `print_mode` | `"5"` | → `ESC_POLI`（见 §1） |
| `subcontract_size` | `3` | 分包参数 → packageSize=1024（§4.3） |
| `is_compress` | `1` | 压缩开启 |
| `is_encipher` | `1` | **加密开启**（§2.3） |
| `is_v1` / `command_version` | `0` / `"3"` | 用 V0 SDK（`HPRTPrinterHelper`），非 V1 |
| `paper_type` | `"1,2"` | 支持 标签纸 / 黑标纸 |
| `print_density` | `"2,3,4"` | 可选浓度档（→ 55/75/95） |
| `tearing_mode` | `5` | 撕纸方式 |
| `loading_position` | `"2"` | 装纸位置 |
| `feats` | `RightSpace,RightOffset1,M1PrintExcept,M1PrintSpeed,HiddenBattery` | 右偏移/打印异常提示/速度/隐藏电量 |
| `label_learning` | `1` | 支持标签学习（`1D 73 65 74 4C`） |
| `other_params` | 纸张学习=`1`、打印自检页=`1B 1B 00`、恢复出厂=`factory_default` | 设备动作 |

`printerDpi` 在运行时换算：`dpm = printerDpi / 25.4 = 8.0 dot/mm`（`DeviceInfo.updatePrintParams()`，
`com/p002prt/base/common/DeviceInfo.java:610-640`），像素尺寸 = mm × 8。

### 6.1 版式相关：RightOffset1（M1 特有）

M1 的 `feats` 含 `RightOffset1`，App 在渲染画布时会做一次**右移**（不是打印机命令）：

```java
// com/p002prt/print/ui/service/DeviceService.java:2393-2405
public final float calculateRightOffset(EditorPanel editorPanel, DeviceInfo deviceInfo, float printerRatioDeviceInPX) {
    if (deviceInfo.isA200U()) return 0.0f;
    if (App.INSTANCE.needRightOffset1()) return UnitHelper.mm2pxFloat$default(printerRatioDeviceInPX, null, 2, null);  // ★ +2mm
    if (!App.INSTANCE.needRightSpace() || MathKt.roundToInt(editorPanel.getAttribute().getWidth() * deviceInfo.dpm) < deviceInfo.printerHeadWidth) return 0.0f;
    return -UnitHelper.mm2pxFloat$default(printerRatioDeviceInPX, null, 2, null);
}
```

`needRightOffset1()` = `feats` 包含 `"RightOffset1"`（`com/p002prt/provider/common/App.java:2184-2187`，
`com/p002prt/print/data/bean/PrinterBean.java:908-909`）。
**含义**：M1 的打印区相对标签纸右偏 2mm，内容需整体右移 2mm（≈16 dot @203dpi）再栅格化，否则内容偏左。

另外两个 feats `M1PrintExcept` / `M1PrintSpeed` 在 Java 代码中**没有任何引用**（仅 `devices.json` 中出现），
属云端 UI 提示，不影响协议。

标签尺寸参考（203dpi, dpm=8）：
| 标签 | 宽 dot | 高 dot | bpr | raw 字节 |
|---|---|---|---|---|
| 40×60 mm | 320 | 480 | 40 | 19200 |
| 40×30 mm | 320 | 240 | 40 | 9600 |
| 50×80 mm | 400 | 640 | 50 | 32000 |
| 48×60 mm（满宽） | 384 | 480 | 48 | 23040 |

---

## 7. 设备参数（浓度/纸张类型/走纸偏移）写入方式

除 §3 的 `1D 73 65 74 63/70` 外，M1 的机型档案里还有"可写参数"（`params` 数组），
App 用 `& V1 setkey` 写入（`HPRTPrinterHelper.setKey`，`HPRTPrinterHelper.java:1466-1492`）：

```java
private final void setKey(String command, String value, int encode, byte[] valueBytes) throws Exception {
    byte[] bytes = "& V1 setkey\r\n".getBytes(Charsets.UTF_8);
    byte[] bArr = {27, 28};
    byte[] key = ByteUtils.INSTANCE.intToBytes2_l(Integer.parseInt(command));   // key LE16
    if (encode == 1) { WriteData(plus(bArr, bytes, new byte[]{1}, key, (byte)encode, (byte)iIntValue)); return; }
    if (encode == 2) { WriteData(plus(bArr, bytes, new byte[]{1}, key, (byte)encode, intToBytes2_l(iIntValue))); return; }
    ...
}
```

即：`1B 1C 26 20 56 31 20 73 65 74 6B 65 79 0D 0A | 01 | keyLo keyHi | len | value[len]`
（`len` 是值字节数；读回用 `getkey`，格式 `…0D 0A 01 keyLo keyHi len`）。

M1 的可写参数（`assets/devices.json` → `params`）：

| 名称 | key (dec/hex) | 值 | 编码 |
|---|---|---|---|
| 纸张类型 | 206 / `0xCE` | 标签纸=2，黑标纸=1 | 1 字节 |
| 打印浓度 | 203 / `0xCB` | 55 / 75 / 95（淡/中/浓），hash_map 另有 1/2/3 档 | 1 字节 |
| 关机时间 | 401 / `0x191` | 0 / 120 / 10（分钟） | 4 字节 |
| 起始页打印位置 | 207 / `0xCF` | 0 / 60 / 12（mm，encode=3） | — |
| 定位后走纸长度 | 208 / `0xD0` | -60 / 60 / 12（mm，encode=2） | 2 字节 |
| 打印速度 | 202 / `0xCA` | 只读 | — |
> **浓度还有第二条路径（打印时实际调用）**：`HPRTPrinterHelper.setDensity(level)`（`HPRTPrinterHelper.java:3912-3928`）
> 直接写 setkey **key=0xCB(203)**：
> ```java
> byte[] bArr = {-53, 0, 1, (byte) level};   // 0xCB 0x00 0x01 level
> WriteData(mergeBytes(new byte[]{27,28,38,32,86,49,32,115,101,116,107,101,121,13,10,1}, bArr));
> // → 1B 1C 26 20 56 31 20 73 65 74 6B 65 79 0D 0A 01 CB 00 01 <level>
> ```
> 调用点：`PrintActivity$loadData$24$2.java:45` → `setDensity(position + 1)`（level 1..4）。
> 也就是说：**打印前的浓度用 setkey 0xCB 设**，`1D 73 65 74 63`（setPollPrintDensity）用于设置页批量下发，
> 两者都指向同一个浓度参数，实现时二选一即可。**【待实测验证】** level 1/2/3/4 与 55/75/95 的对应关系。


写完后要发 `& V1 do "save_param_zone"\r\n` 保存（错误码回 1 字节，`HPRTPrinterHelper.java:2143-2167`）。

示例（设为标签纸 + 浓度 75）：
```
1B 1C 26 20 56 31 20 73 65 74 6B 65 79 0D 0A 01 CE 00 01 02
1B 1C 26 20 56 31 20 73 65 74 6B 65 79 0D 0A 01 CB 00 01 4B
1B 1C 26 20 56 31 20 64 6F 20 22 73 61 76 65 5F 70 61 72 61 6D 5F 7A 6F 6E 65 22 0D 0A
```

> **【待实测验证】** 参数写是否同样需要先握手/XOR；以及打印时是否必须设置 key 206（很多固件有掉电保存，设置一次即可）。

---

## 8. 对照：TSPL 分支（**不是 M1**，勿混用）

若你的实现需要兼容其它机型（`print_mode=2`），TSPL 分支在：
`com/p002prt/print/utils/printer/TSPLPrinterManager.java` + `HPRTAndroidSDK/HPRTPrinterHelper.java`：

```
SIZE <w>mm,<h>mm\r\n            HPRTPrinterHelper.java:1621-1639  (w=printerHeadWidth/dpm, h=bitmapHeight/dpm)
CLS\r\n                          HPRTPrinterHelper.java:1641-1657
SET TEAR ON\r\n                  TSPLPrinterManager.java:89-93（可选）
BITMAP x,y,wBytes,h,<model>,    HPRTPrinterHelper.java:1700-1791
    若 model∈{3,16,17,18} 且压缩后≤原长： + LE32(压缩长) + LZO数据
    否则                                  + 原始数据（model=0 分支）
    注意：TSPL 分支会先整体取反 (isNegate=true, TSPLPrinterManager.java:96) → **1=白、0=黑**
PRINT n,1\r\n                    HPRTPrinterHelper.java:1797-1814
```
该分支的位图来自 `GetImageDataRasterMono`（Floyd–Steinberg 抖动，`PrinterDataCore.java:497-594`），
与 M1 的 `CreatePrintBitmapData`（无抖动、1=黑）不同。

---

## 9. 待实测验证 / TODO 清单

1. **BLE 与 SPP 字节流是否等价**：App 走 SPP，我们走 `ff00/ff02`。若 BLE 上无反应，先检查：
   - 是否必须先发握手并切 XOR（§2.3）；
   - `ff02` 是否要求 20 字节对齐 + 等 `01 01` ACK；
   - 是否需要先写 `ff01`（notify 使能 CCCD `00002902`）。
2. **`set mm` 回包**：M1 回 `4F4B` 还是 `4E47`？加密状态是否跨连接保持。
3. **`1D 66 C0 03`（960）语义**：是否正确停位、是否多送纸；备选：`1B 1B 01 <h>` 或 `1D 66` 后跟标签高度。
4. **是否需要 `1B 40` 初始化**：App 因 `subcontract=3` 跳过；裸实现可先试"不发"，不行再发。
5. **`1B 61 n` 方向定义**：0/1/2 对应左/中/右还是其它，以实测为准。
6. **LZO 兼容性**：打印机是否接受标准 `lzo1x_1_compress` 输出（有些固件只用自家 LZO 但格式一致）；
   若帧被拒，可改 `CompressMode=3` 之外的路径 —— 即直接用 `1D 76 30 30` 头 + **未压缩**数据测试
   （把 lzoLen 设为 rawLen，数据填 raw）观察是否出图。
7. **多份打印**：App 是"整个帧流 + 走纸"重复 perCount 次；确认打印机不会自动重复。
8. **`print_density` 档位 vs `1D 73 65 74 63` 值与 `setkey 0xCB`** 哪个生效。
9. **max label length**：M1 是否限制 120mm（960 dot）以内。

---

## 10. 证据索引（反编译输出路径均相对 `re-apk/jadx/sources/`）

| 结论 | 文件:行 |
|---|---|
| M1 档案（dpi/宽度/加密/压缩/分包） | `../../apk-unzip/assets/devices.json`（`model_id=315`） |
| `print_mode → instruct` 映射 | `com/p002prt/print/data/bean/PrinterBean.java:784-810` |
| 指令集分发到 ESCPOLI | `com/p002prt/print/utils/printer/PrintManagerFacade.java:90-172` |
| M1 打印主流程 | `com/p002prt/print/utils/printer/ESCPOLIPrinterManager.java:75-125`（含 :97 `isM1()`、:101 printBitmapPackage、:106/:108 走纸） |
| 浓度档位映射 | `.../ESCPOLIPrinterManager.java:15-28` |
| 连接/加密开关/SPP | `com/p002prt/print/utils/printer/BasePrintManager.java:172-195, 250-300, 401-430` |
| `isShack` 默认 true | `HPRTAndroidSDK/HPRTConst.java:42` |
| 握手 `1B 1C set mm n m` | `HPRTAndroidSDK/Check.java:10-52`；`HPRTPrinterHelper.java:4010-4049` |
| XOR 加密实现 | `HPRTAndroidSDK/BTOperator.java:352-387`；`setKey` :749-752 |
| SPP UUID / BLE 特征动态选择 | `HPRTAndroidSDK/BTOperator.java:186, 260-279, 976-988` |
| BLE 分包/MTU/间隔 | `com/bhm/ble/data/Constants.java`、`com/bhm/ble/attribute/BleOptions.java:260-262`、`com/bhm/ble/request/BleWriteRequest.java` |
| 位图打包（1=黑） | `HPRTAndroidSDK/PrinterDataCore.java:596-644` |
| LZO 帧头 | `HPRTAndroidSDK/PrinterDataCore.java:184-207`（同 :117-142） |
| 分包/整行切块 | `HPRTAndroidSDK/HPRTPrinterHelper.java:3227-3257`；`HPRTAndroidSDK/Tools.java:38-56` |
| 单帧路径 | `HPRTAndroidSDK/HPRTPrinterHelper.java:976-1017` |
| 对齐/浓度/纸张/学习/走纸 | `HPRTPrinterHelper.java:576-609, 2066-2099, 2806-2818, 2964-2971, 2192-2209` |
| 状态查询与位定义 | `HPRTPrinterHelper.java:1933-1952`；`com/p002prt/print/data/bean/PrinterStatus.java:194-203` |
| 参数 setkey | `HPRTPrinterHelper.java:1466-1492`；`CommandData.java:36-42` |
| TSPL 分支（对照） | `.../printer/TSPLPrinterManager.java:69-203`；`HPRTPrinterHelper.java:1621-1814` |

> 说明：`com/prt` 包在 jadx 输出中被重命名为 `com/p002prt`（`prt` 与反编译器的占位冲突），语义不变；
> 原始 dex 中的类名仍是 `com.prt.print...`（见 `classes10.dex` 字符串）。
