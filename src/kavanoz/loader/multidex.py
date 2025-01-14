from androguard.core.bytecodes.apk import APK
import zlib
from androguard.core.bytecodes.dvm import DalvikVMFormat, EncodedMethod
import re
import ctypes
import string
from kavanoz.unpack_plugin import Unpacker


def unsigned_rshift(val, n):
    unsigned_integer = val % 0x100000000
    return unsigned_integer >> n


def unsigned_lshift(val, n):
    unsigned_integer = val % 0x100000000
    return unsigned_integer << n


class LoaderMultidex(Unpacker):
    ProtectKey = ""

    def __init__(self, apk_obj, dvms, output_dir):
        super().__init__(
            "loader.multidex", "Unpacker for multidex variants", apk_obj, dvms, output_dir
        )

    def start_decrypt(self, native_lib: str = ""):
        self.logger.info("Starting to decrypt")
        z = self.apk_object.get_android_manifest_xml()
        if z != None:
            f = z.find("application")
            childs = f.getchildren()
            self.ProtectKey = None
            for child in childs:
                if child.tag == "meta-data":
                    if (
                        child.attrib["{http://schemas.android.com/apk/res/android}name"]
                        == "ProtectKey"
                    ):
                        self.ProtectKey = child.attrib[
                            "{http://schemas.android.com/apk/res/android}value"
                        ]
            if self.ProtectKey != None:
                if self.find_decrypt_protect_arrays():
                    self.logger.info("Found key in manifest/xor")
                    return

        self.decrypted_payload_path = None
        zip_function = self.find_zip_function()
        if zip_function is not None:
            _function, dvm = zip_function
            variable = self.extract_variable_from_zip(_function, dvm)
            if variable is not None:
                key = self.find_clinit_target_variable(variable)
                if key is not None:
                    if self.brute_assets(key):
                        if self.is_really_unpacked():
                            self.logger.info("fully unpacked")
                        else:
                            self.logger.info("not fully unpacked")
                        return
        else:
            self.logger.info("Cannot find zip function")
            self.logger.info("Second plan for zipper")
            self.second_plan()

    def second_plan(self):
        application = self.apk_object.get_attribute_value("application", "name")
        if application == None:
            return None

        application_smali = "L" + application.replace(".", "/") + ";"
        target_method = self.find_method(application_smali, "<init>")
        if target_method == None:
            return None
        smali_str = self.get_smali(target_method)
        """
        sget-object v0, Lb;->f:Ljava/lang/String;
        invoke-static {v0}, Lc;->b(Ljava/lang/String;)Ljava/lang/String;
        move-result-object v0
        """
        match = re.findall(
            r"sget-object [vp]\d+, (L[^;]+;->[^ ]+) Ljava/lang/String;\s+"
            r"invoke-static {?[vp]\d+}?, L[^;]+;->[^\(]+\(Ljava/lang/String;\)Ljava/lang/String",
            smali_str,
        )
        # print(match)
        # self.for_fun(match[0])
        for matched_field in match:
            key = self.find_clinit_target_variable(matched_field)
            if key != None:
                xor_k = 0x6033
                tmp_key = "".join(chr(xor_k ^ ord(c)) for c in key)
                self.logger.info(f"Is this a key ??? {tmp_key}")
                if tmp_key is not None:
                    if all(c in string.printable for c in tmp_key):
                        asset_list = self.apk_object.get_files()
                        for filepath in asset_list:
                            f = self.apk_object.get_file(filepath)
                            if self.solve_encryption(
                                f, tmp_key
                            ) or self.solve_encryption2(f, tmp_key):
                                return True
                    else:
                        return False

        return target_method

    def find_zip_function(self):
        target_method = None
        for d in self.dvms:
            for c in d.get_classes():
                for m in c.get_methods():
                    if (
                        m.get_descriptor()
                        == "(Ljava/util/zip/ZipFile; Ljava/util/zip/ZipEntry; Ljava/io/File; Ljava/lang/String;)V"
                    ):
                        self.logger.info("Found method")
                        target_method = m
                        return target_method, d
        return None

    def find_decrypt_protect_arrays(self):
        for d in self.dvms:
            for c in d.get_classes():
                for m in c.get_methods():
                    if m.get_descriptor() == "(I)[C":
                        self.logger.info("Found decrypt protect arrays method")
                        smali_str = self.get_smali(m)
                        """
                        const/16 v6, 11
                        const/4 v5, 3
                        const/4 v4, 2
                        const/4 v3, 1
                        const/4 v2, 0
                        if-eqz v7, +1d6
                        if-eq v7, v3, +1c8
                        if-eq v7, v4, +1bd
                        if-eq v7, v5, +5
                        new-array v0, v2, [C
                        return-object v0
                        const/16 v0, 75
                        oto/16 -1b5
                        new-array v0, v3, [C
                        const/16 v1, 24627
                        int-to-char v1, v1
                        aput-char v1, v0, v2
                        goto/16 -1be
                        new-array v0, v4, [C
                        const/16 v1, 12293
                        aput-char v1, v0, v2
                        const/16 v1, 12294
                        aput-char v1, v0, v3
                        goto/16 -1ca
                    """
                        match = re.findall(
                            r"new-array [vp]\d+, [vp]\d+, \[C\s+"
                            r"const/16 [vp]\d+, (-?\d+)\s+"
                            r"int-to-char [vp]\d+, [vp]\d+\s+"
                            r"aput-char [vp]\d+, [vp]\d+, [vp]\d+\s+"
                            r"goto/16 -?[a-f0-9]+\s+",
                            smali_str,
                        )
                        for m in match:
                            try:
                                xor_k = int(m)
                            except:
                                self.logger.info("bad match", m)
                                continue
                            if self.ProtectKey != None:
                                tmp_key = "".join(
                                    chr(xor_k ^ ord(c)) for c in self.ProtectKey
                                )
                                if self.brute_assets(tmp_key):
                                    return True
                            else:
                                self.logger.info("no protect key found in manifest..")
                        else:
                            self.logger.info("Could not find with key size of 1")

    def extract_variable_from_zip(self, target_method: EncodedMethod, dvm):
        smali_str = self.get_smali(target_method)
        """
        5 invoke-virtual v3, v0, Ljava/util/zip/ZipOutputStream;->putNextEntry(Ljava/util/zip/ZipEntry;)V
        6 sget-object v0, Lcom/icecream/sandwich/c;->l Ljava/lang/String;
        7 new-instance v4, Ljava/util/zip/InflaterInputStream;
        """
        match = re.findall(
            r"invoke-virtual [vp]\d+, [vp]\d+, [vp]\d+, Ljava/util/zip/ZipEntry;->setTime\(J\)V\s+"
            r"invoke-virtual {?[vp]\d+, [vp]\d+}?, L[^;]+;->[^\(]+\(Ljava/util/zip/ZipEntry;\)V\s+"
            r"sget-object [vp]\d+, (L[^;]+;->[^\(]+) Ljava/lang/String;\s+",
            smali_str,
        )
        if len(match) == 0:
            self.logger.info(
                f"Unable to extract variable from {target_method.get_name()}"
            )
            self.logger.info("Exiting ...")
            return None
        if len(match) == 1:
            self.logger.info(f"Found variable ! : {match[0]}")
            method = self.find_method(target_method.class_name, "<clinit>")
            if method:
                smali_str = self.get_smali(method)
                key_variable = re.findall(
                    r"sget-object [vp]\d+, (L[^;]+;->[^\s]+) Ljava/lang/String;\s+"
                    f"sput-object v0, {match[0]} Ljava/lang/String;",
                    smali_str,
                )
                if len(key_variable) == 1:
                    self.logger.info(
                        f"Found key variable from zip class <clinit> {key_variable[0]}"
                    )
                    return key_variable[0]
                else:
                    self.logger.info("Not found key variable from clinit")
                    return None
        else:
            self.logger.info("Something is wrong .. 🤔")
            self.logger.info("Found multiple ?? : {match}")
            return None

    def for_fun(self, variable_string):
        variable_class, variable_field = variable_string.split("->")
        key_class = self.find_class_in_dvms(variable_class)
        if key_class == None:
            self.logger.info(f"No key class found {key_class}")
            return None

        self.logger.info(f"Key class found ! {key_class}")
        key_clinit = self.find_method(variable_class, "<clinit>")
        if key_clinit is not None:
            smali_str = self.get_smali(key_clinit)
            # self.logger.info(smali_str)
            match = re.findall(
                r"const-string [vp]\d+, '(.*)'\s+" rf"sput-object [vp]\d+, .*\s+",
                smali_str,
            )
            for m in match:
                xor_k = 0x6033
                tmp_key = "".join(chr(xor_k ^ ord(c)) for c in m)
                self.logger.info(f"zaa??? {tmp_key}")

    def find_clinit_target_variable(self, variable_string):
        variable_class, variable_field = variable_string.split("->")
        key_class = self.find_class_in_dvms(variable_class)
        if key_class == None:
            self.logger.info(f"No key class found {key_class}")
            return None

        self.logger.info(f"Key class found ! {key_class}")
        key_clinit = self.find_method(variable_class, "<clinit>")
        if key_clinit is not None:
            smali_str = self.get_smali(key_clinit)
            # self.logger.info(smali_str)
            match = re.findall(
                r"const-string [vp]\d+, '(.*)'\s+"
                rf"sput-object [vp]\d+, {variable_string} Ljava/lang/String;",
                smali_str,
            )
            if len(match) == 0:
                self.logger.info(
                    f"Cannot find string definition in clinit for target variable {variable_string}"
                )
                # If its using apkprotecttor, we can try some other method
                match = re.findall(
                    r"const-string(?:/jumbo)? [vp]\d+, '(.*)'\s+"
                    r"invoke-static [vp]\d+, [vp]\d+, L[^;]+;->[^\(]+\(Ljava\/lang\/String; I\)Ljava\/lang\/String;\s+"
                    r"move-result-object [vp]\d+\s+"
                    rf"sput-object [vp]\d+, {variable_string} Ljava/lang/String;",
                    smali_str,
                )
                if len(match) == 0:
                    match = re.findall(
                        r"const-string(?:/jumbo)? [vp]\d+, '(.*)'\s+"
                        r"invoke-static [vp]\d+, L[^;]+;->[^\(]+\(Ljava\/lang\/String;\)Ljava\/lang\/String;\s+"
                        r"move-result-object [vp]\d+\s+"
                        rf"sput-object [vp]\d+, {variable_string} Ljava/lang/String;",
                        smali_str,
                    )

                if len(match) == 1:
                    xor_k = 0x6033
                    tmp_key = "".join(chr(xor_k ^ ord(c)) for c in match[0])
                    self.logger.info(f"Is this a key ??? {tmp_key}")
                    return tmp_key
            if len(match) == 1:
                self.logger.info(f"Found key !  {match[0]}")
                return match[0]
            else:
                self.logger.info(f"Multiple key ? {match}")
        if key_clinit is None:
            self.logger.info(f"No clinit for {variable_class}")
        return None

    def brute_assets(self, key: str):
        self.logger.info(f"Starting brute-force inflate {key}")
        asset_list = self.apk_object.get_files()
        for filepath in asset_list:
            f = self.apk_object.get_file(filepath)
            if self.solve_encryption(f, key) or self.solve_encryption2(f, key):
                self.logger.info("Decryption finished!!")
                return self.decrypted_payload_path
        return None

    def solve_encryption2(self, file_data, key):
        if len(file_data) < 8 or len(key) < 12:
            return False

        if file_data[0] == 0x78 and file_data[1] == 0x9C:
            try:
                encrypted = zlib.decompress(file_data)
            except Exception as e:
                self.logger.error(e)
                return False
        else:
            encrypted = file_data

        iArr = []  # 2
        iArr2 = []  # 4
        iArr3 = [None] * 27  # 27
        iArr4 = []  # 3
        key = [ord(c) for c in key]
        iArr = [key[8] | (key[9] << 16), key[11] << 16 | key[10]]
        iArr2.extend(
            [
                key[0] | (key[1] << 16),
                key[2] | (key[3] << 16),
                key[4] | (key[5] << 16),
                key[6] | (key[7] << 16),
            ]
        )
        iArr3[0] = iArr2[0]
        iArr4.extend([iArr2[1], iArr2[2], iArr2[3]])
        i2 = iArr2[0]
        i = 0
        while i < 26:
            i3 = i % 3
            iArr4[i3] = (
                (
                    (unsigned_rshift(ctypes.c_int32(iArr4[i3]).value, 8))
                    | ctypes.c_int32((iArr4[i3]) << 24).value
                )
                + i2
            ) ^ i
            i2 = (
                ctypes.c_int32(i2 << 3).value
                | (unsigned_rshift(ctypes.c_int32(i2).value, 29))
            ) ^ ctypes.c_int32(iArr4[i3]).value
            i += 1
            iArr3[i] = i2

        decrypted_bytes = bytearray()
        z = 0
        for b in encrypted:
            if z % 8 == 0:
                h0 = iArr[0]
                h1 = iArr[1]
                for k in iArr3:
                    tmp0 = ((unsigned_rshift(h1, 8) | (h1 << 24) & 0xFFFFFFFF) + h0) ^ k
                    tmp1 = ((h0 << 3) & 0xFFFFFFFF | unsigned_rshift(h0, 29)) ^ tmp0
                    h0 = tmp1 & 0xFFFFFFFF
                    h1 = tmp0 & 0xFFFFFFFF
                iArr[0] = h0
                iArr[1] = h1
            b ^= iArr[int((z % 8) / 4)] >> (8 * (z % 4)) & 0xFF
            if (z == 0 and b != 0x78) or (z == 1 and b != 0x9C):
                return False
            z += 1
            decrypted_bytes.append(b)
        if self.check_and_write_file(decrypted_bytes):
            self.logger.info("Found in second algo finished")
            return True
        return False

    def solve_encryption(self, file_data: bytes, key: str):
        if len(file_data) < 8 or len(key) < 12:
            return False
        if file_data[0] == 0x78 and file_data[1] == 0x9C:
            try:
                encrypted = zlib.decompress(file_data)
            except Exception as e:
                self.logger.error(e)
                return False
        else:
            encrypted = file_data
        decrypted_bytes = bytearray()
        indexes = [0, 0, 0, 0, 1, 1, 1, 1]
        bits = [0, 8, 16, 24]
        c = [ord(x) for x in key]
        poolArr = [(c[9] << 16) | c[8], (c[11] << 16) | c[10]]
        check_0 = (poolArr[indexes[0]]) >> bits[0] & 0xFF ^ encrypted[0]
        check_1 = (poolArr[indexes[0]]) >> bits[0] & 0xFF ^ encrypted[1]
        if check_0 != 0x78 and check_1 != 0x9C:
            return False
        for i, b in enumerate(encrypted):
            b ^= (poolArr[indexes[i % 8]]) >> bits[i % 4] & 0xFF
            decrypted_bytes.append(b)

        if self.check_and_write_file(decrypted_bytes):
            self.logger.info("Found in first algo")
            return True
        else:
            return False
