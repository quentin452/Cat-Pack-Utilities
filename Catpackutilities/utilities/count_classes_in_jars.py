import os
import glob
from zipfile import ZipFile, BadZipFile

def count_classes_in_jars(rootdir):
    jar_counts = []
    total_classes = 0

    for jar_path in glob.glob(os.path.join(rootdir, '**/*.jar'), recursive=True):
        try:
            with ZipFile(jar_path) as jar:
                class_count = sum(1 for name in jar.namelist() if name.endswith('.class'))
                jar_counts.append((jar_path, class_count))
                total_classes += class_count
        except BadZipFile:
            print(f"Skipped {jar_path}: not a valid JAR")
            continue

    jar_counts.sort(key=lambda x: x[1], reverse=True)
    print("\nNombre de classes par mod JAR (du plus grand au plus petit) :")
    for path, count in jar_counts:
        print(f"{count:5} classes - {os.path.basename(path)}")

    print(f"\n🧮 Total de classes chargées dans tous les JARs : {total_classes:,} classes")
    return jar_counts

if __name__ == "__main__":
    rootdir = input("Chemin du dossier racine contenant les JAR: ").strip()
    if os.path.isdir(rootdir):
        count_classes_in_jars(rootdir)
    else:
        print("Chemin invalide.")

    input("\nAppuyez sur Entrée pour fermer...")
