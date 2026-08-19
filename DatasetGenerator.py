import csv
import math
import os

# --- KONFIGURATION ---
input_file = r"D:\projects\DataViewer\datasets_original\Record_2026-07-23_07-43-17.csv"     # Deine originale CSV-Datei (Startwerte)
output_dir = r"D:\projects\DataViewer\datasets"   # Ordner für die generierten CSVs
NUM_FRAMES = 60                    # Wie viele Dateien/Zeitschritte generiert werden sollen
TARGET_TEMP = 50.0                  # Das Ziel-Plateau in °C
K_FACTOR = 0.04                     # Steuert die Erwärmungsgeschwindigkeit (Anstieg der e-Funktion)


def load_base_data(file_path: str):
    """Lädt die Basis-CSV in eine Matrix aus Floats."""
    data = []
    with open(file_path, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=';')
        for row in reader:
            parsed_row = []
            for val in row:
                val_str = val.strip()
                if val_str:
                    parsed_row.append(float(val_str.replace(',', '.')))
            if parsed_row:
                data.append(parsed_row)
    return data


def save_frame(data: list, frame_idx: int, output_dir: str):
    """Speichert eine Matrix als CSV ab (deutsches Format mit Komma)."""
    file_name = os.path.join(output_dir, f"Record_2026-07-23_08-{frame_idx-1:02d}-00.csv")
    with open(file_name, mode='w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=';')
        for row in data:
            formatted_row = [f"{val:.1f}".replace('.', ',') for val in row]
            writer.writerow(formatted_row)


def generate_heating_sequence():
    os.makedirs(output_dir, exist_ok=True)
    base_data = load_base_data(input_file)
    
    print(f"Starte Generierung von {NUM_FRAMES} Frames...")

    # Über alle Zeitschritte (t) iterieren
    for t in range(NUM_FRAMES):
        # Fortschritt der Erwärmung von 0.0 (Start) bis ~1.0 (Plateau nahe 50°C)
        # 1 - e^(-k * t)
        heating_factor = 1.0 - math.exp(-K_FACTOR * t)
        
        frame_data = []
        for row in base_data:
            new_row = []
            for start_temp in row:
                # Erwärmung berechnen: T_start + (50 - T_start) * factor
                if start_temp < TARGET_TEMP:
                    current_temp = start_temp + (TARGET_TEMP - start_temp) * heating_factor
                else:
                    current_temp = start_temp  # Falls ein Pixel schon über 50°C war
                    
                new_row.append(current_temp)
            frame_data.append(new_row)
            
        save_frame(frame_data, t + 1, output_dir)

    print(f"Fertig! {NUM_FRAMES} Dateien wurden im Ordner '{output_dir}' erstellt.")

if __name__ == "__main__":
    generate_heating_sequence()