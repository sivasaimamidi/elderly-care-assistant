import json
import os
import sys
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("Elderly Care Helper")

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "elderly_care_data.json")

def load_db() -> dict:
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"patients": {}}

def save_db(db: dict):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

@mcp.tool()
def get_elderly_status(patient_name: str) -> str:
    """Retrieve the full status profile for the elderly patient including medications, history, logs, and appointments.

    Args:
        patient_name: The name of the patient (e.g. 'john_doe').
    """
    db = load_db()
    patient = db.get("patients", {}).get(patient_name.lower())
    if not patient:
        return f"Patient '{patient_name}' not found."
    return json.dumps(patient, indent=2)

@mcp.tool()
def update_medication(patient_name: str, medication_name: str, dosage: str, frequency: str, purpose: str) -> str:
    """Add or update a medication in the patient's schedule.

    Args:
        patient_name: The name of the patient (e.g. 'john_doe').
        medication_name: The name of the medication.
        dosage: The dosage (e.g. '20mg').
        frequency: How often to take it (e.g. 'Once daily').
        purpose: What condition it treats.
    """
    db = load_db()
    patients = db.setdefault("patients", {})
    patient = patients.setdefault(patient_name.lower(), {
        "medications": [],
        "medication_history": [],
        "wellbeing_logs": [],
        "appointments": []
    })
    
    # Check if medication already exists, if so update it, otherwise append
    updated = False
    for med in patient["medications"]:
        if med["name"].lower() == medication_name.lower():
            med["dosage"] = dosage
            med["frequency"] = frequency
            med["purpose"] = purpose
            updated = True
            break
            
    if not updated:
        patient["medications"].append({
            "name": medication_name,
            "dosage": dosage,
            "frequency": frequency,
            "purpose": purpose
        })
        
    save_db(db)
    action = "Updated" if updated else "Added"
    return f"Successfully {action.lower()} medication: {medication_name} ({dosage}, {frequency}) for {patient_name}."

@mcp.tool()
def log_medication_taken(patient_name: str, medication_name: str, time_taken: str) -> str:
    """Log that a medication was taken at a specific time.

    Args:
        patient_name: The name of the patient (e.g. 'john_doe').
        medication_name: The name of the medication.
        time_taken: The time it was taken (e.g. '2026-07-02 08:00 AM').
    """
    db = load_db()
    patients = db.setdefault("patients", {})
    patient = patients.setdefault(patient_name.lower(), {
        "medications": [],
        "medication_history": [],
        "wellbeing_logs": [],
        "appointments": []
    })
    
    patient["medication_history"].append({
        "medication": medication_name,
        "time_taken": time_taken,
        "status": "taken"
    })
    save_db(db)
    return f"Logged medication '{medication_name}' as taken by {patient_name} at {time_taken}."

@mcp.tool()
def add_wellbeing_log(patient_name: str, systolic: int, diastolic: int, heart_rate: int, temperature: float, symptoms: str) -> str:
    """Log vital signs and a well-being assessment for the patient.

    Args:
        patient_name: The name of the patient.
        systolic: Blood pressure systolic value (mmHg).
        diastolic: Blood pressure diastolic value (mmHg).
        heart_rate: Heart rate (bpm).
        temperature: Body temperature (Fahrenheit).
        symptoms: Any symptoms or general notes.
    """
    db = load_db()
    patients = db.setdefault("patients", {})
    patient = patients.setdefault(patient_name.lower(), {
        "medications": [],
        "medication_history": [],
        "wellbeing_logs": [],
        "appointments": []
    })
    
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    
    status = "normal"
    if systolic > 140 or diastolic > 90 or temperature > 100.4 or heart_rate > 100:
        status = "warning"
        
    log_entry = {
        "timestamp": timestamp,
        "systolic": systolic,
        "diastolic": diastolic,
        "heart_rate": heart_rate,
        "temperature": temperature,
        "symptoms": symptoms,
        "status": status
    }
    patient["wellbeing_logs"].append(log_entry)
    save_db(db)
    return f"Successfully logged well-being metrics for {patient_name}. Status: {status.upper()}."

@mcp.tool()
def book_appointment(patient_name: str, doctor_name: str, date_time: str, reason: str) -> str:
    """Book a new doctor appointment or visit.

    Args:
        patient_name: The name of the patient.
        doctor_name: The name of the doctor or specialist.
        date_time: The scheduled date and time (e.g. '2026-07-10 10:00 AM').
        reason: The reason for the visit.
    """
    db = load_db()
    patients = db.setdefault("patients", {})
    patient = patients.setdefault(patient_name.lower(), {
        "medications": [],
        "medication_history": [],
        "wellbeing_logs": [],
        "appointments": []
    })
    
    patient["appointments"].append({
        "doctor_name": doctor_name,
        "date_time": date_time,
        "reason": reason
    })
    save_db(db)
    return f"Successfully booked appointment with {doctor_name} for {patient_name} on {date_time}."

if __name__ == "__main__":
    # Start the FastMCP stdio server
    mcp.run()
