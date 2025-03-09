import os
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from dotenv import load_dotenv

# Import your DB function to store a request
from db import add_request

# Load environment variables from .env
load_dotenv()

ACCOUNT_SID = os.getenv("ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")

client = Client(ACCOUNT_SID, TWILIO_AUTH_TOKEN)
app = Flask(__name__)

# In-memory session store: { caller_number: {"step": int, "type": str, "material": str, ...} }
CALL_SESSIONS = {}

@app.route("/voice", methods=["GET", "POST"])
def voice_wizard():
    """
    Main entry point for inbound calls. We do a step-by-step 'wizard' flow:
    1) Ask if "Я продаю" or "Я покупаю"
    2) Ask material
    3) Ask quantity
    4) Ask city
    5) Ask additional info
    6) Confirm and store in DB
    """
    caller = request.values.get("From")  # e.g. "+1234567890"
    if not caller:
        # If Twilio didn't provide a caller, just return an error message
        vr = VoiceResponse()
        vr.say("Извините, не удалось определить ваш номер. Завершаю звонок.")
        return Response(str(vr), mimetype="text/xml")

    # Get or create a session dict for this caller
    session = CALL_SESSIONS.setdefault(caller, {
        "step": 0,
        "type": "",
        "material": "",
        "quantity": "",
        "city": "",
        "info": ""
    })

    # Get the speech result from the user (Twilio param "SpeechResult")
    user_input = request.values.get("SpeechResult", "").strip().lower()

    # Build a TwiML response
    vr = VoiceResponse()

    # Decide which step we are on
    step = session["step"]

    if step == 0:
        # Step 0: Ask if user is selling or buying
        gather = Gather(
            input="speech",
            timeout=5,
            action="/voice",  # We'll come back to this same route
            speech_timeout="auto"
        )
        gather.say("Здравствуйте! Скажите, Я продаю, или Я покупаю?")
        vr.append(gather)
        vr.redirect("/voice")  # If no speech input, redirect to same step
        session["step"] = 1  # Next time we come here, we'll parse the input
        return Response(str(vr), mimetype="text/xml")

    elif step == 1:
        # We interpret the user_input as selling or buying
        if "продаю" in user_input:
            session["type"] = "продажа"
        elif "покупаю" in user_input:
            session["type"] = "закупка"
        else:
            # Not understood
            gather = Gather(input="speech", timeout=5, speech_timeout="auto", action="/voice")
            gather.say("Извините, не понял. Скажите, Я продаю, или Я покупаю?")
            vr.append(gather)
            vr.redirect("/voice")
            return Response(str(vr), mimetype="text/xml")

        # Move to step 2: ask material
        session["step"] = 2
        gather = Gather(input="speech", timeout=5, speech_timeout="auto", action="/voice")
        gather.say("Какой материал? Например, Медь, Алюминий, Латунь?")
        vr.append(gather)
        vr.redirect("/voice")
        return Response(str(vr), mimetype="text/xml")

    elif step == 2:
        # Store material
        if user_input:
            session["material"] = user_input
        else:
            session["material"] = "не указан"

        # Step 3: ask quantity
        session["step"] = 3
        gather = Gather(input="speech", timeout=5, speech_timeout="auto", action="/voice")
        gather.say("Укажите количество. Например, 5 тонн, 100 килограммов и т. д.")
        vr.append(gather)
        vr.redirect("/voice")
        return Response(str(vr), mimetype="text/xml")

    elif step == 3:
        # Store quantity
        if user_input:
            session["quantity"] = user_input
        else:
            session["quantity"] = "не указано"

        # Step 4: ask city
        session["step"] = 4
        gather = Gather(input="speech", timeout=5, speech_timeout="auto", action="/voice")
        gather.say("Из какого вы города? Например, Москва или Санкт-Петербург.")
        vr.append(gather)
        vr.redirect("/voice")
        return Response(str(vr), mimetype="text/xml")

    elif step == 4:
        # Store city
        if user_input:
            session["city"] = user_input
        else:
            session["city"] = "не указан"

        # Step 5: ask additional info
        session["step"] = 5
        gather = Gather(input="speech", timeout=5, speech_timeout="auto", action="/voice")
        gather.say("Добавьте дополнительную информацию. Или скажите ничего, если нет дополнительной информации.")
        vr.append(gather)
        vr.redirect("/voice")
        return Response(str(vr), mimetype="text/xml")

    elif step == 5:
        # Store info
        if user_input:
            session["info"] = user_input
        else:
            session["info"] = "не указана"

        # Step 6: confirm
        session["step"] = 6
        # Summarize the request
        summary = (
            f"Тип: {session['type']}\n"
            f"Материал: {session['material']}\n"
            f"Количество: {session['quantity']}\n"
            f"Город: {session['city']}\n"
            f"Доп. информация: {session['info']}\n"
        )
        # Ask user to confirm
        gather = Gather(input="speech", timeout=5, speech_timeout="auto", action="/voice")
        gather.say("Вы хотите разместить заявку? Скажите да или нет. Вот ваши данные: " + summary)
        vr.append(gather)
        vr.redirect("/voice")
        return Response(str(vr), mimetype="text/xml")

    elif step == 6:
        # Check confirmation
        if "да" in user_input or "давай" in user_input or "конечно" in user_input:
            # Store in DB
            add_request(
                user_id=1,  # or some real user_id if you have phone->user mapping
                req_type=session["type"],
                material=session["material"],
                quantity=session["quantity"],
                city=session["city"],
                info=session["info"]
            )
            vr.say("Заявка успешно размещена! Спасибо. Всего доброго.")
        else:
            vr.say("Заявка отменена. Всего доброго.")

        # Cleanup session
        CALL_SESSIONS.pop(caller, None)
        return Response(str(vr), mimetype="text/xml")


# (Optional) You can still have /make_call or other endpoints if needed
# for outbound calls, etc.

if __name__ == "__main__":
    # Run as a normal Flask server
    app.run(debug=True, port=5000)
