import os
import zipfile
import json
import glob
from collections import defaultdict
import logging
from datetime import datetime

def setup_logging():
    """Configure logging system"""
    log_folder = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'duplicate_logs')
    os.makedirs(log_folder, exist_ok=True)
    
    log_file = os.path.join(log_folder, f'duplicate_scan_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    return log_file

def is_suspicious_modid(modid):
    """Detect suspicious modids that should not be considered duplicates"""
    suspicious_modids = [
        'minecraft', 'examplemod', 'example', 'test', 'modid',
        'forge', 'fml', 'mcp'
    ]
    return modid.lower() in suspicious_modids

def extract_embedded_mods(jar):
    """Scan a jar (ZipFile) for embedded mods (mcmod.info / mods.toml / package-info.class)"""
    import re

    embedded_mods = []

    try:
        for name in jar.namelist():
            # mcmod.info or mods.toml
            if name.endswith("mcmod.info") or name.endswith("META-INF/mods.toml"):
                try:
                    with jar.open(name) as f:
                        content = f.read().decode("utf-8", errors="ignore")
                        # mcmod.info
                        if name.endswith("mcmod.info"):
                            try:
                                data = json.loads(content)
                                if isinstance(data, list):
                                    for mod in data:
                                        modid = mod.get("modid")
                                        if modid:
                                            embedded_mods.append({
                                                "modid": modid,
                                                "name": mod.get("name"),
                                                "version": mod.get("version"),
                                                "source": name
                                            })
                                elif isinstance(data, dict):
                                    modid = data.get("modid")
                                    if modid:
                                        embedded_mods.append({
                                            "modid": modid,
                                            "name": data.get("name"),
                                            "version": data.get("version"),
                                            "source": name
                                        })
                            except Exception:
                                pass
                        # mods.toml
                        elif name.endswith("mods.toml"):
                            current_mod = {}
                            for line in content.splitlines():
                                line = line.strip()
                                if line.startswith("modId="):
                                    current_mod["modid"] = line.split("=", 1)[1].strip().strip('"')
                                elif line.startswith("displayName="):
                                    current_mod["name"] = line.split("=", 1)[1].strip().strip('"')
                                elif line.startswith("version="):
                                    current_mod["version"] = line.split("=", 1)[1].strip().strip('"')
                            if current_mod.get("modid"):
                                current_mod["source"] = name
                                embedded_mods.append(current_mod)
                except Exception:
                    continue

            # package-info.class for FML API
            elif name.endswith("package-info.class"):
                try:
                    data = jar.read(name)
                    text = data.decode("latin1", errors="ignore")
                    if "cpw/mods/fml/common/API" in text:
                        api_match = re.search(
                            r'@API\s*\(\s*apiVersion\s*=\s*"([^"]+)",\s*owner\s*=\s*"([^"]+)",\s*provides\s*=\s*"([^"]+)"\s*\)',
                            text
                        )
                        if api_match:
                            version, owner, provides = api_match.groups()
                            embedded_mods.append({
                                "modid": provides.lower(),
                                "name": provides,
                                "version": version,
                                "owner": owner,
                                "source": name
                            })
                except Exception:
                    continue
    except Exception:
        pass

    return embedded_mods

def extract_mod_info(jar_path):
    """Extract main mod info (mcmod.info, mods.toml, etc.)"""
    info = {
        "modid": None,
        "name": None,
        "version": None,
        "filename": os.path.basename(jar_path),
        "path": jar_path,
        "size": os.path.getsize(jar_path),
        "suspicious": False,
        "embedded_mods": []
    }

    try:
        with zipfile.ZipFile(jar_path, "r") as jar:
            # Search for mcmod.info or mods.toml
            for name in jar.namelist():
                if name.endswith("mcmod.info"):
                    try:
                        data = json.loads(jar.read(name).decode("utf-8"))
                        if isinstance(data, list):
                            data = data[0]
                        info["modid"] = data.get("modid")
                        info["name"] = data.get("name")
                        info["version"] = data.get("version")
                    except Exception:
                        pass
                elif name.endswith("mods.toml"):
                    try:
                        content = jar.read(name).decode("utf-8")
                        for line in content.splitlines():
                            line = line.strip()
                            if line.startswith("modId"):
                                info["modid"] = line.split("=", 1)[1].strip().strip('"')
                            elif line.startswith("displayName"):
                                info["name"] = line.split("=", 1)[1].strip().strip('"')
                            elif line.startswith("version"):
                                info["version"] = line.split("=", 1)[1].strip().strip('"')
                    except Exception:
                        pass

            # Check embedded mods with the jar object
            info["embedded_mods"] = extract_embedded_mods(jar)

    except Exception as e:
        logging.warning(f"Unable to read {jar_path}: {e}")

    if info["modid"] and is_suspicious_modid(info["modid"]):
        info["suspicious"] = True

    return info

def find_duplicate_mods(mods_folder):
    """Find duplicate mods in the specified folder"""
    logging.info(f"Scanning mods folder: {mods_folder}")

    # Find all JAR files
    jar_files = glob.glob(os.path.join(mods_folder, "**/*.jar"), recursive=True)
    logging.info(f"Found {len(jar_files)} JAR files")

    # Extract information from each mod
    mods_info = []
    for jar_path in jar_files:
        info = extract_mod_info(jar_path)
        if info:
            mods_info.append(info)

    # Group by modid
    mods_by_id = defaultdict(list)
    for mod in mods_info:
        if mod['modid']:
            mods_by_id[mod['modid']].append(mod)

    # Identify duplicates
    duplicates = {}
    for modid, mod_list in mods_by_id.items():
        if len(mod_list) > 1:
            duplicates[modid] = mod_list
    
    return duplicates, mods_info

def analyze_duplicates(duplicates):
    """Analyze duplicates and categorize issues"""
    problems = {
        'suspicious_mods': defaultdict(list),
        'real_duplicates': defaultdict(list),
        'different_versions': defaultdict(list)
    }
    
    for modid, mod_list in duplicates.items():
        if is_suspicious_modid(modid) or any(mod['suspicious'] for mod in mod_list):
            problems['suspicious_mods'][modid] = mod_list
        else:
            versions = set(mod['version'] for mod in mod_list if mod['version'])
            if len(versions) > 1:
                problems['different_versions'][modid] = mod_list
            else:
                problems['real_duplicates'][modid] = mod_list
    
    return problems

def print_improved_report(problems, all_mods):
    """Display an improved report with categorization"""
    print(f"\n🎯 MOD ANALYSIS REPORT")
    print(f"Total mods analyzed: {len(all_mods)}")

    # Suspicious mods
    if problems['suspicious_mods']:
        print(f"\n🔴 SUSPICIOUS MODS ({len(problems['suspicious_mods'])} groups):")
        print("These mods have problematic IDs (minecraft, examplemod, etc.)")
        for modid, mod_list in problems['suspicious_mods'].items():
            print(f"\n┌── {modid}")
            for mod in mod_list[:5]:  # Limit display
                print(f"│   📁 {mod['filename']}")
            if len(mod_list) > 5:
                print(f"│   ... et {len(mod_list) - 5} autres")
            print("└─────────────────")

    # Real duplicates
    if problems['real_duplicates']:
        print(f"\n🟡 REAL DUPLICATES ({len(problems['real_duplicates'])} mods):")
        for modid, mod_list in problems['real_duplicates'].items():
            print(f"\n┌── {modid}")
            for i, mod in enumerate(mod_list, 1):
                version = mod['version'] or 'Unknown version'
                print(f"│   {i}. {mod['filename']} (v{version})")
            print("└─────────────────")

    # Different versions
    if problems['different_versions']:
        print(f"\n🔵 DIFFERENT VERSIONS ({len(problems['different_versions'])} mods):")
        for modid, mod_list in problems['different_versions'].items():
            versions = set(mod['version'] for mod in mod_list if mod['version'])
            print(f"\n┌── {modid} ({len(versions)} versions)")
            mod_list.sort(key=lambda x: x['version'] or '0', reverse=True)
            for mod in mod_list[:3]:  # Show the 3 most recent
                version = mod['version'] or 'Unknown version'
                print(f"│   📦 {mod['filename']} (v{version})")
            if len(mod_list) > 3:
                print(f"│   ... and {len(mod_list) - 3} older versions")
            print("└─────────────────")

def generate_detailed_report(problems, all_mods, output_path):
    """Generate a detailed report in a file"""
    report = []
    report.append("=== DETAILED MOD DUPLICATE REPORT ===")
    report.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"Total mods analyzed: {len(all_mods)}")
    report.append("")

    # Suspicious mods
    if problems['suspicious_mods']:
        report.append("🔴 SUSPICIOUS MODS (Problematic IDs):")
        for modid, mod_list in problems['suspicious_mods'].items():
            report.append(f"\n--- {modid} ---")
            for mod in mod_list:
                report.append(f"  File: {mod['filename']}")
                report.append(f"  Version: {mod.get('version', 'Unknown')}")
                report.append(f"  Size: {mod['size'] / (1024*1024):.1f}MB")
                if mod['embedded_mods']:
                    report.append(f"  Embedded mods: {len(mod['embedded_mods'])}")
                report.append(f"  Path: {mod['path']}")
                report.append("")

    # Real duplicates
    if problems['real_duplicates']:
        report.append("\n🟡 REAL DUPLICATES:")
        for modid, mod_list in problems['real_duplicates'].items():
            report.append(f"\n--- {modid} ---")
            for mod in mod_list:
                report.append(f"  File: {mod['filename']}")
                report.append(f"  Version: {mod.get('version', 'Unknown')}")
                report.append(f"  Size: {mod['size'] / (1024*1024):.1f}MB")
                report.append("")

    # Different versions
    if problems['different_versions']:
        report.append("\n🔵 DIFFERENT VERSIONS:")
        for modid, mod_list in problems['different_versions'].items():
            versions = set(mod['version'] for mod in mod_list if mod['version'])
            report.append(f"\n--- {modid} ({len(versions)} versions) ---")
            mod_list.sort(key=lambda x: x['version'] or '0', reverse=True)
            for mod in mod_list:
                report.append(f"  File: {mod['filename']}")
                report.append(f"  Version: {mod.get('version', 'Unknown')}")
                report.append(f"  Size: {mod['size'] / (1024*1024):.1f}MB")
                report.append("")

    # Write the report
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report))
    
    return output_path

def main():
    log_file = setup_logging()
    
    print("=== CAT PACK DUPLICATE MOD FINDER V2 ===")
    
    while True:
        mods_folder = input("\nEntrez le chemin du dossier 'mods' (ou 'exit' pour quitter): ").strip()
        
        if mods_folder.lower() == 'exit':
            break
        
        if not os.path.isdir(mods_folder):
            print("❌ Folder not found. Please try again.")
            continue

        print(f"\n🔍 Advanced analysis of {mods_folder}...")

        try:
            duplicates, all_mods = find_duplicate_mods(mods_folder)
            problems = analyze_duplicates(duplicates)
            print_improved_report(problems, all_mods)

            # Generate a detailed report
            report_folder = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'duplicate_reports')
            os.makedirs(report_folder, exist_ok=True)
            report_file = os.path.join(report_folder, f'improved_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
            
            generate_detailed_report(problems, all_mods, report_file)
            print(f"\n📊 Detailed report saved: {report_file}")
            print(f"📝 Logs: {log_file}")
            
        except Exception as e:
            logging.error(f"Error during analysis: {e}")
            print(f"❌ Error: {e}")

        choice = input("\nDo you want to analyze another folder? (y/n): ").lower()
        if choice != 'y':
            break

if __name__ == "__main__":
    main()