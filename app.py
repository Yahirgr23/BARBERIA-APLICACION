# app.py
# app.py (al principio del archivo)
from io import BytesIO
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from flask import make_response
from collections import Counter
import os
from datetime import datetime, timedelta, date, time
from functools import wraps
import calendar # Necesario para monthrange en reportes mensuales
import pytz
# app.py (al principio del archivo)
from itertools import groupby



TARGET_TIMEZONE = pytz.timezone('America/Mexico_City')


# En app.py, AÑADE esta nueva función

def formatear_fecha_espanol(dt):
    """Toma un objeto datetime y devuelve una cadena de texto con la fecha en español."""
    dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    
    # weekday() devuelve 0 para Lunes, 6 para Domingo
    nombre_dia = dias[dt.weekday()]
    nombre_mes = meses[dt.month - 1]
    
    return f"{nombre_dia}, {dt.day} de {nombre_mes.lower()} de {dt.year}"


# En app.py, AÑADE esta nueva función

def get_logical_utc_date():
    """Determina la fecha de trabajo lógica basada en un corte a las 3 AM (hora local)."""
    # Usamos la hora UTC para la comparación, 3 AM en CST/CDT es ~9:00 UTC
    # Para ser simples y robustos, usaremos 9:00 UTC como la hora de corte.
    if datetime.utcnow().hour < 9:
        # Si son antes de las 9:00 UTC, todavía es el "día" anterior en UTC
        return datetime.utcnow().date() - timedelta(days=1)
    else:
        # Si son después de las 9:00 UTC, ya es el nuevo "día"
        return datetime.utcnow().date()


from flask import (
    Flask, render_template, redirect, url_for, flash, request, session, abort, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, current_user, login_required
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func, extract # Para sumas y extracción de partes de fechas

# --- Configuración de la App ---
app = Flask(__name__)
app.config['SECRET_KEY'] = '987466fcb8d9de6d5205060ce82387c81394f43d5591c2f8' 
instance_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance')
if not os.path.exists(instance_path):
    os.makedirs(instance_path)
# En app.py, reemplaza la línea de configuración de la base de datos

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    # Si estamos en Render (o cualquier entorno con DATABASE_URL), la usamos.
    # Render puede usar 'postgres://' pero SQLAlchemy prefiere 'postgresql://'
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    # Si no, seguimos usando nuestra base de datos local de SQLite.
    instance_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance')
    if not os.path.exists(instance_path):
        os.makedirs(instance_path)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(instance_path, 'barberia.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "Por favor, inicia sesión para acceder a esta página."
login_manager.login_message_category = "info"

# Definir la Zona Horaria de Destino (tu zona local)
TARGET_TIMEZONE = pytz.timezone('America/Mexico_City')


def format_datetime_local(utc_dt, fmt="%d/%m/%Y %H:%M"):
    if not utc_dt:
        return ""
    # Si es un objeto date (sin hora), simplemente formatéalo
    if not hasattr(utc_dt, 'hour'):
         return utc_dt.strftime(fmt if "%H:%M" not in fmt else "%d/%m/%Y") # Evitar error si fmt espera hora

    # Si el datetime es "naive" (sin información de zona horaria), asumimos que es UTC
    if utc_dt.tzinfo is None:
        utc_dt = pytz.utc.localize(utc_dt)

    local_dt = utc_dt.astimezone(TARGET_TIMEZONE)
    return local_dt.strftime(fmt)

app.jinja_env.filters['localtime'] = format_datetime_local

# --- Modelos de la Base de Datos ---
class Barbero(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    nombre_usuario = db.Column(db.String(80), unique=True, nullable=False)
    contrasena_hash = db.Column(db.String(256), nullable=False)
    nombre_completo = db.Column(db.String(120), nullable=False)
    porcentaje_comision = db.Column(db.Float, nullable=False, default=0.5)
    rol = db.Column(db.String(20), nullable=False, default='barbero')
    cortes = db.relationship('Corte', backref='autor_barbero', lazy='dynamic') # Cambiado a lazy='dynamic'
    pagos = db.relationship('PagoSemanal', backref='beneficiario_barbero', lazy='dynamic') # Cambiado a lazy='dynamic'

    def set_password(self, password):
        self.contrasena_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.contrasena_hash, password)

    def __repr__(self):
        return f'<Barbero {self.nombre_usuario}>'

class Servicio(db.Model): # Definido ANTES de Corte
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False)
    precio = db.Column(db.Float, nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    cortes_realizados = db.relationship('Corte', backref='servicio_prestado', lazy='dynamic')

    def __repr__(self):
        return f'<Servicio {self.nombre} - ${self.precio}>'

# En app.py, añade esta nueva clase de modelo

class ComisionServicio(db.Model):
    __tablename__ = 'comision_servicio'
    id = db.Column(db.Integer, primary_key=True)
    barbero_id = db.Column(db.Integer, db.ForeignKey('barbero.id'), nullable=False)
    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=False)
    porcentaje = db.Column(db.Float, nullable=False)

    # Relaciones para acceder a los objetos Barbero y Servicio fácilmente
    barbero = db.relationship('Barbero', backref=db.backref('comisiones_especificas', cascade="all, delete-orphan"))
    servicio = db.relationship('Servicio', backref=db.backref('comisiones_recibidas', cascade="all, delete-orphan"))

    # Asegura que no haya entradas duplicadas para el mismo barbero y servicio
    __table_args__ = (db.UniqueConstraint('barbero_id', 'servicio_id', name='_barbero_servicio_uc'),)

    def __repr__(self):
        return f'<ComisionServicio Barbero:{self.barbero_id} Servicio:{self.servicio_id} -> {self.porcentaje:.0%}>'

class Corte(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    barbero_id = db.Column(db.Integer, db.ForeignKey('barbero.id'), nullable=False)
    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=False)
    precio_registrado = db.Column(db.Float, nullable=False) 
    fecha_hora_corte = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<Corte {self.id} - ServicioID: {self.servicio_id} Precio: ${self.precio_registrado}>'

# app.py (Modifica la clase PagoSemanal)

class PagoSemanal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    barbero_id = db.Column(db.Integer, db.ForeignKey('barbero.id'), nullable=False)
    
    # Identificador único para el periodo de pago, ej: "2025-W23_MonSat" o "2025-06-08_Sun"
    periodo_pago_id = db.Column(db.String(50), nullable=False) 
    # Descripción legible del periodo, ej: "Lunes a Sábado (02/06/2025 - 07/06/2025)" o "Domingo 08/06/2025"
    descripcion_periodo_pago = db.Column(db.String(100)) 
    
    total_bruto_periodo = db.Column(db.Float, nullable=False) # Antes total_bruto_semana
    monto_comision_barbero_periodo = db.Column(db.Float, nullable=False) # Antes monto_comision_barbero
    fecha_pago_registrado = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Crear un índice compuesto para asegurar que un barbero solo tenga un pago por periodo_pago_id
    __table_args__ = (db.UniqueConstraint('barbero_id', 'periodo_pago_id', name='uq_barbero_periodo_pago'),)


    def __repr__(self):
        return f'<PagoSemanal {self.id} Barbero: {self.barbero_id} Periodo: {self.periodo_pago_id}>'
    

# app.py (en la sección de Modelos de la Base de Datos)

class DiaFinalizado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha_finalizada = db.Column(db.Date, unique=True, nullable=False) # Fecha que se está cerrando
    total_bruto_global = db.Column(db.Float, nullable=False)
    total_comisiones_barberos = db.Column(db.Float, nullable=False)
    total_para_barberia = db.Column(db.Float, nullable=False)
    fecha_hora_cierre_efectivo = db.Column(db.DateTime, nullable=False, default=datetime.utcnow) # Cuándo se hizo clic en el botón
    admin_id_cierre = db.Column(db.Integer, db.ForeignKey('barbero.id'), nullable=True) # Quién lo cerró (opcional)

    admin_que_cerro = db.relationship('Barbero', foreign_keys=[admin_id_cierre])

    def __repr__(self):
        return f'<DiaFinalizado {self.fecha_finalizada.strftime("%Y-%m-%d")}>'


@login_manager.user_loader
def load_user(user_id):
    return Barbero.query.get(int(user_id))

# --- Función Auxiliar para Comisiones (REUTILIZABLE) ---
def get_commission_rate(barbero_obj, servicio_id):
    """Busca la comisión específica. Si no la encuentra, devuelve la del barbero por defecto."""
    # Primero, intentamos encontrar una comisión específica para este servicio y barbero
    comision_especifica = ComisionServicio.query.filter_by(
        barbero_id=barbero_obj.id,
        servicio_id=servicio_id
    ).first()
    
    # Si encontramos una comisión específica y es mayor a 0, la usamos.
    if comision_especifica and comision_especifica.porcentaje > 0:
        return comision_especifica.porcentaje
    
    # Si no, usamos la comisión por defecto del barbero.
    return barbero_obj.porcentaje_comision

# En app.py, añade esta nueva función auxiliar

# Reemplaza esta función
def get_start_date_for_open_day():
    """Determina la fecha de inicio para los reportes diarios, usando la fecha lógica."""
    today = get_logical_utc_date() # CAMBIO AQUÍ
    check_date = today - timedelta(days=1)
    
    for _ in range(30):
        last_closed_day = DiaFinalizado.query.filter_by(fecha_finalizada=check_date).first()
        if last_closed_day:
            return check_date + timedelta(days=1)
        check_date -= timedelta(days=1)
        
    return check_date + timedelta(days=1)


# --- Decorador para roles ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.rol != 'admin':
            flash("Acceso no autorizado. Se requieren permisos de administrador.", "danger")
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# --- Context Processor (para variables globales en plantillas) ---
@app.context_processor
def inject_now():
    # Obtener la hora actual en UTC y luego convertirla a la zona horaria local
    utc_now = datetime.utcnow()
    # Hacemos utc_now consciente de su zona horaria (UTC)
    aware_utc_now = pytz.utc.localize(utc_now)
    # Convertimos a la zona horaria local
    local_now = aware_utc_now.astimezone(TARGET_TIMEZONE)
    return {'now': local_now} # 'now' ahora estará en tu hora local
# --- Rutas ---
@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.rol == 'admin':
            return redirect(url_for('admin_dashboard'))
        else: # Asumimos barbero u otro rol definido
            return redirect(url_for('barbero_dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        nombre_usuario = request.form.get('nombre_usuario')
        contrasena = request.form.get('contrasena')
        barbero = Barbero.query.filter_by(nombre_usuario=nombre_usuario).first()
        
        if barbero and barbero.check_password(contrasena):
            login_user(barbero)
            flash('Inicio de sesión exitoso.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash('Nombre de usuario o contraseña incorrectos.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Has cerrado sesión.', 'info')
    return redirect(url_for('login'))

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

# --- Páginas de Error ---
@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(403)
def forbidden_access(e):
    return render_template('errors/403.html'), 403

# --- Rutas del Barbero ---
# Reemplaza esta función completa en tu app.py
# En app.py, reemplaza la función barbero_dashboard completa

# Reemplaza esta función
# En app.py, reemplaza también esta función completa:

# En app.py, reemplaza tu función barbero_dashboard con esta versión final:

# app.py (Reemplaza esta función completa)

# app.py

# En app.py, reemplaza tu función barbero_dashboard completa con esta versión final:

# En app.py, reemplaza la función barbero_dashboard completa con esta:

# En app.py, reemplaza la función barbero_dashboard completa con esta:

# En app.py, reemplaza la función barbero_dashboard completa con esta:


@app.route('/barbero/mis_comisiones')
@login_required
def mis_comisiones():
    if current_user.rol != 'barbero':
        abort(403)
    
    def get_commission_rate(barbero_obj, servicio_id):
        comision_especifica = ComisionServicio.query.filter_by(
            barbero_id=barbero_obj.id,
            servicio_id=servicio_id
        ).first()
        if comision_especifica and comision_especifica.porcentaje > 0:
            return comision_especifica.porcentaje
        return barbero_obj.porcentaje_comision

    servicios_activos = Servicio.query.filter_by(activo=True).order_by(Servicio.nombre).all()
    
    lista_comisiones = []
    for servicio in servicios_activos:
        tasa_comision = get_commission_rate(current_user, servicio.id)
        lista_comisiones.append({
            'nombre': servicio.nombre,
            'precio': servicio.precio,
            'comision': tasa_comision
        })
        
    return render_template('barbero/mis_comisiones.html', 
                           lista_comisiones=lista_comisiones,
                           comision_defecto=current_user.porcentaje_comision)

@app.route('/barbero/registrar_corte', methods=['GET', 'POST'])
@login_required
def barbero_registrar_corte():
    if current_user.rol != 'barbero':
        abort(403)

    # ---- INICIO DE LÍNEAS DE DEPURACIÓN ----
    print("\n--- DEPURANDO /barbero/registrar_corte ---")

    # Función auxiliar para obtener la comisión correcta (específica o por defecto)
    def get_commission_rate(barbero_obj, servicio_id):
        comision_especifica = ComisionServicio.query.filter_by(
            barbero_id=barbero_obj.id,
            servicio_id=servicio_id
        ).first()
        if comision_especifica and comision_especifica.porcentaje > 0:
            return comision_especifica.porcentaje
        return barbero_obj.porcentaje_comision

    # Vamos a ver qué encuentra esta consulta
    servicios_activos = Servicio.query.filter_by(activo=True).order_by(Servicio.nombre).all()
    print(f"Paso 1: ¿Se encontraron servicios activos? -> {servicios_activos}")
    
    servicios_con_comision = []
    if servicios_activos:
        print("Paso 2: El bucle 'for' va a comenzar porque se encontraron servicios.")
        for servicio in servicios_activos:
            tasa_comision = get_commission_rate(current_user, servicio.id)
            servicios_con_comision.append({
                'servicio': servicio,
                'comision': tasa_comision
            })
    else:
        print("Paso 2: El bucle 'for' NO se ejecutará porque la lista de servicios activos está vacía.")


    # ---- FIN DE LÍNEAS DE DEPURACIÓN ----

    if request.method == 'POST':
        # ... (la lógica del POST no la tocamos) ...
        servicio_id_str = request.form.get('servicio_id')
        if not servicio_id_str:
            flash('Debes seleccionar un servicio.', 'danger')
        else:
            try:
                servicio_id = int(servicio_id_str)
                servicio_seleccionado = Servicio.query.get(servicio_id)

                if not servicio_seleccionado or not servicio_seleccionado.activo:
                    flash('Servicio no válido o no disponible.', 'danger')
                else:
                    nuevo_corte = Corte(barbero_id=current_user.id, servicio_id=servicio_seleccionado.id, precio_registrado=servicio_seleccionado.precio, fecha_hora_corte=datetime.utcnow())
                    db.session.add(nuevo_corte)
                    db.session.commit()
                    flash(f'Corte de "{servicio_seleccionado.nombre}" (${servicio_seleccionado.precio:.2f}) registrado exitosamente.', 'success')
                    return redirect(url_for('barbero_dashboard'))
            except ValueError:
                flash('ID de servicio inválido.', 'danger')
            except Exception as e:
                db.session.rollback()
                flash(f'Error al registrar el corte: {str(e)}', 'danger')

    print(f"Paso 3: Lista final 'servicios_con_comision' que se envía a la plantilla -> {servicios_con_comision}")
    print("-------------------------------------------\n")

    return render_template('barbero/registrar_corte.html', servicios_con_comision=servicios_con_comision)

# --- Rutas del Administrador ---
# En app.py, reemplaza la función admin_dashboard completa con esta:

# Pega esta función completa y corregida en tu app.py

# En app.py, reemplaza tu función admin_dashboard con esta versión corregida:

# En app.py, reemplaza la función admin_dashboard con esta

# Reemplaza esta función
# En app.py, reemplaza la función barbero_dashboard completa con esta:

# En app.py, reemplaza la función barbero_dashboard completa con esta versión corregida:

# Pega esta única versión correcta en tu app.py

# Pega esta función completa de vuelta en tu app.py
# (Puedes ponerla antes de la función barbero_historial)

@app.route('/barbero/dashboard')
@login_required
def barbero_dashboard():
    if current_user.rol != 'barbero':
        abort(403)

    # --- 1. CÁLCULO DE GANANCIA SEMANAL (para la tarjeta superior) ---
    hoy = date.today()
    inicio_semana_dt = hoy - timedelta(days=hoy.weekday())
    fin_semana_dt = inicio_semana_dt + timedelta(days=6)
    inicio_semana_utc = TARGET_TIMEZONE.localize(datetime.combine(inicio_semana_dt, time.min)).astimezone(pytz.utc)
    fin_semana_utc = TARGET_TIMEZONE.localize(datetime.combine(fin_semana_dt, time.max)).astimezone(pytz.utc)
    cortes_semana = Corte.query.filter(
        Corte.barbero_id == current_user.id,
        Corte.fecha_hora_corte.between(inicio_semana_utc, fin_semana_utc)
    ).all()
    ganancia_semanal = sum(c.precio_registrado * get_commission_rate(current_user, c.servicio_id) for c in cortes_semana)

    # --- 2. ENFOQUE EXCLUSIVO EN EL DÍA DE TRABAJO ACTUAL ---
    fecha_actual_logica = get_logical_utc_date()
    inicio_hoy = datetime.combine(fecha_actual_logica, time.min)
    fin_hoy = datetime.combine(fecha_actual_logica, time.max)
    
    cortes_hoy = Corte.query.filter(
        Corte.barbero_id == current_user.id,
        Corte.fecha_hora_corte.between(inicio_hoy, fin_hoy)
    ).order_by(Corte.fecha_hora_corte.desc()).all()

    ganancia_hoy = sum(c.precio_registrado * get_commission_rate(current_user, c.servicio_id) for c in cortes_hoy)

    # Procesamos los cortes de hoy para la lista
    cortes_hoy_con_ganancia = []
    for corte in cortes_hoy:
        ganancia = corte.precio_registrado * get_commission_rate(current_user, corte.servicio_id)
        cortes_hoy_con_ganancia.append({'corte': corte, 'ganancia': ganancia})

    return render_template('barbero/dashboard.html', 
                           ganancia_semanal=ganancia_semanal,
                           ganancia_hoy=ganancia_hoy,
                           cortes_hoy=cortes_hoy_con_ganancia)

# Pega esta función completa de vuelta en tu app.py

@app.route('/barbero/historial')
@login_required
def barbero_historial():
    if current_user.rol != 'barbero':
        abort(403)
    
    periodo = request.args.get('periodo', 'semana') # Semana por defecto
    hoy = date.today()
    titulo_periodo = "Esta Semana"
    
    # Define el rango de la semana actual por defecto
    start_date = hoy - timedelta(days=hoy.weekday())
    end_date = start_date + timedelta(days=6)

    if periodo == 'mes':
        titulo_periodo = "Este Mes"
        start_date = hoy.replace(day=1)
        _, ultimo_dia_mes = calendar.monthrange(hoy.year, hoy.month)
        end_date = hoy.replace(day=ultimo_dia_mes)
    elif periodo == 'personalizado':
        fecha_inicio_str = request.args.get('fecha_inicio')
        fecha_fin_str = request.args.get('fecha_fin')
        if fecha_inicio_str and fecha_fin_str:
            try:
                start_date = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
                titulo_periodo = f"Del {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"
            except ValueError:
                # Si las fechas son inválidas, vuelve a la semana por defecto
                periodo = 'semana'
                titulo_periodo = "Esta Semana"
                start_date = hoy - timedelta(days=hoy.weekday())
                end_date = start_date + timedelta(days=6)
        else: # Si no hay fechas, vuelve a la semana
            periodo = 'semana'

    inicio_periodo = datetime.combine(start_date, time.min)
    fin_periodo = datetime.combine(end_date, time.max)

    cortes_periodo = Corte.query.filter(
        Corte.barbero_id == current_user.id,
        Corte.fecha_hora_corte.between(inicio_periodo, fin_periodo)
    ).order_by(Corte.fecha_hora_corte.desc()).all()

    cortes_agrupados = {}
    if cortes_periodo:
        for key, group in groupby(cortes_periodo, key=lambda c: format_datetime_local(c.fecha_hora_corte, '%Y-%m-%d')):
            fecha_dt = datetime.strptime(key, '%Y-%m-%d')
            fecha_formateada = formatear_fecha_espanol(fecha_dt)
            cortes_con_ganancia = [{'corte': c, 'ganancia': c.precio_registrado * get_commission_rate(current_user, c.servicio_id)} for c in group]
            cortes_agrupados[fecha_formateada] = cortes_con_ganancia

    total_bruto_periodo = sum(c.precio_registrado for c in cortes_periodo)
    total_comision_periodo = sum(c.precio_registrado * get_commission_rate(current_user, c.servicio_id) for c in cortes_periodo)

    return render_template('barbero/historial.html', 
                           cortes_agrupados=cortes_agrupados,
                           total_bruto_periodo=total_bruto_periodo,
                           total_comision_periodo=total_comision_periodo,
                           titulo_periodo=titulo_periodo,
                           periodo_seleccionado=periodo,
                           fecha_inicio_form=start_date.strftime('%Y-%m-%d'),
                           fecha_fin_form=end_date.strftime('%Y-%m-%d'))


# Pega esta función completa de vuelta en tu app.py
# (Puedes ponerla junto a las otras rutas de administrador)

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    # --- Cálculos para el Día de Hoy (UTC) ---
    hoy_utc = datetime.utcnow().date()
    inicio_hoy = datetime.combine(hoy_utc, datetime.min.time())
    fin_hoy = datetime.combine(hoy_utc, datetime.max.time())
    
    cortes_hoy = Corte.query.filter(Corte.fecha_hora_corte.between(inicio_hoy, fin_hoy)).all()
    ingresos_hoy = sum(c.precio_registrado for c in cortes_hoy)
    servicios_hoy = len(cortes_hoy)

    # --- Cálculo de Top Barberos de Hoy ---
    ingresos_por_barbero = {}
    for corte in cortes_hoy:
        nombre_barbero = corte.autor_barbero.nombre_completo
        ingresos_por_barbero[nombre_barbero] = ingresos_por_barbero.get(nombre_barbero, 0) + corte.precio_registrado
    top_barberos = sorted(ingresos_por_barbero.items(), key=lambda item: item[1], reverse=True)[:3]

    # --- Cálculo de Servicios Populares de Hoy ---
    contador_servicios = Counter(c.servicio_prestado.nombre for c in cortes_hoy)
    servicios_populares = contador_servicios.most_common(3)

    # --- Cálculos para Semana y Mes ---
    hoy = date.today()
    inicio_semana_dt = hoy - timedelta(days=hoy.weekday())
    fin_semana_dt = inicio_semana_dt + timedelta(days=6)
    inicio_semana = datetime.combine(inicio_semana_dt, datetime.min.time())
    fin_semana = datetime.combine(fin_semana_dt, datetime.max.time())
    ingresos_semana = db.session.query(func.sum(Corte.precio_registrado)).filter(Corte.fecha_hora_corte.between(inicio_semana, fin_semana)).scalar() or 0.0

    inicio_mes_dt = hoy.replace(day=1)
    _, ultimo_dia_mes = calendar.monthrange(hoy.year, hoy.month)
    fin_mes_dt = hoy.replace(day=ultimo_dia_mes)
    inicio_mes = datetime.combine(inicio_mes_dt, datetime.min.time())
    fin_mes = datetime.combine(fin_mes_dt, datetime.max.time())
    ingresos_mes = db.session.query(func.sum(Corte.precio_registrado)).filter(Corte.fecha_hora_corte.between(inicio_mes, fin_mes)).scalar() or 0.0

    # --- Usamos nuestra función para formatear la fecha ---
    fecha_formateada = formatear_fecha_espanol(datetime.now(TARGET_TIMEZONE))

    return render_template('admin/dashboard.html',
                           ingresos_hoy=ingresos_hoy,
                           servicios_hoy=servicios_hoy,
                           ingresos_semana=ingresos_semana,
                           ingresos_mes=ingresos_mes,
                           top_barberos=top_barberos,
                           servicios_populares=servicios_populares,
                           fecha_formateada=fecha_formateada)

@app.route('/admin/barberos')
@login_required
@admin_required
def admin_barberos():
    # Primero, contamos cuántos servicios activos hay en total
    total_servicios_activos = Servicio.query.filter_by(activo=True).count()

    barberos_activos = Barbero.query.filter_by(rol='barbero').order_by(Barbero.nombre_completo).all()
    
    barberos_data = []
    for barbero in barberos_activos:
        # Por cada barbero, contamos cuántas comisiones específicas tiene guardadas
        comisiones_configuradas = ComisionServicio.query.filter_by(barbero_id=barbero.id).count()
        
        barberos_data.append({
            'barbero': barbero,
            'total_servicios': total_servicios_activos,
            'comisiones_configuradas': comisiones_configuradas
        })

    return render_template('admin/barberos.html', barberos_data=barberos_data)
# Reemplaza esta función
# En app.py, reemplaza esta función completa:

# app.py

# app.py (Reemplaza esta función completa)

# app.py (Reemplaza esta función para asegurar consistencia)

@app.route('/admin/resumen_diario_barberos')
@login_required
@admin_required
def admin_resumen_diario_barberos():
    local_now = datetime.now(TARGET_TIMEZONE)
    fecha_actual_local = local_now.date()

    hoy_inicio_local = datetime.combine(fecha_actual_local, datetime.min.time())
    hoy_fin_local = datetime.combine(fecha_actual_local, datetime.max.time())
    
    hoy_inicio_utc = TARGET_TIMEZONE.localize(hoy_inicio_local).astimezone(pytz.utc)
    hoy_fin_utc = TARGET_TIMEZONE.localize(hoy_fin_local).astimezone(pytz.utc)

    barberos_activos = Barbero.query.filter_by(rol='barbero').order_by(Barbero.nombre_completo).all()
    
    resumen_barberos = []
    total_bruto_dia_global = 0.0
    total_comisiones_dia_global = 0.0
    total_parabarberia_dia_global = 0.0 # <-- NOMBRE CONSISTENTE

    for barbero in barberos_activos:
        cortes_del_barbero_hoy = Corte.query.filter(
            Corte.barbero_id == barbero.id,
            Corte.fecha_hora_corte >= hoy_inicio_utc,
            Corte.fecha_hora_corte <= hoy_fin_utc
        ).all()

        bruto_barbero = sum(c.precio_registrado for c in cortes_del_barbero_hoy)
        comision_neta_barbero = bruto_barbero * barbero.porcentaje_comision
        para_barberia_de_este_barbero = bruto_barbero - comision_neta_barbero

        resumen_barberos.append({
            'nombre': barbero.nombre_completo,
            'id': barbero.id,
            'total_bruto_dia': bruto_barbero,
            'porcentaje_comision': barbero.porcentaje_comision,
            'comision_neta_dia': comision_neta_barbero,
            'monto_para_barberia': para_barberia_de_este_barbero, # <-- NOMBRE CONSISTENTE
            'cantidad_cortes': len(cortes_del_barbero_hoy)
        })
        
        total_bruto_dia_global += bruto_barbero
        total_comisiones_dia_global += comision_neta_barbero
        total_parabarberia_dia_global += para_barberia_de_este_barbero # <-- NOMBRE CONSISTENTE

    dia_finalizado_hoy = DiaFinalizado.query.filter_by(fecha_finalizada=fecha_actual_local).first()

    return render_template('admin/resumen_diario_barberos.html',
                           resumen_barberos=resumen_barberos,
                           fecha_actual_local=fecha_actual_local,
                           total_bruto_dia_global=total_bruto_dia_global,
                           total_comisiones_dia_global=total_comisiones_dia_global,
                           total_parabarberia_dia_global=total_parabarberia_dia_global, # <-- NOMBRE CONSISTENTE
                           dia_ya_finalizado=dia_finalizado_hoy is not None
                           )

@app.route('/admin/finalizar_dia', methods=['POST'])
@login_required
@admin_required
def admin_finalizar_dia():
    # --- INICIO DE CAMBIO: Lógica de Fechas Basada en Zona Horaria Local ---
    local_now = datetime.now(TARGET_TIMEZONE)
    fecha_a_finalizar_local = local_now.date() # Esta es la fecha que cerraremos

    # Verificar si el día local ya fue finalizado
    existente = DiaFinalizado.query.filter_by(fecha_finalizada=fecha_a_finalizar_local).first()
    if existente:
        flash(f"El día {fecha_a_finalizar_local.strftime('%d/%m/%Y')} ya ha sido finalizado previamente.", "warning")
        return redirect(url_for('admin_resumen_diario_barberos'))

    # Definir el rango del día local en UTC para la consulta
    hoy_inicio_local = datetime.combine(fecha_a_finalizar_local, datetime.min.time())
    hoy_fin_local = datetime.combine(fecha_a_finalizar_local, datetime.max.time())
    hoy_inicio_utc = TARGET_TIMEZONE.localize(hoy_inicio_local).astimezone(pytz.utc)
    hoy_fin_utc = TARGET_TIMEZONE.localize(hoy_fin_local).astimezone(pytz.utc)
    # --- FIN DE CAMBIO ---
    
    # Recalcular los totales del día para asegurar consistencia
    cortes_del_dia = Corte.query.filter(
        Corte.fecha_hora_corte >= hoy_inicio_utc,
        Corte.fecha_hora_corte <= hoy_fin_utc
    ).all()

    # El resto de la función para calcular totales y guardar el registro es casi igual...
    total_bruto_dia_global = sum(c.precio_registrado for c in cortes_del_dia)
    total_comisiones_dia_global = 0.0
    
    barberos_con_actividad_hoy = db.session.query(Barbero.id, Barbero.porcentaje_comision).filter(Barbero.id.in_([c.barbero_id for c in cortes_del_dia])).distinct().all()
    barbero_comisiones_map = {b_id: porcentaje for b_id, porcentaje in barberos_con_actividad_hoy}

    for corte in cortes_del_dia:
        if corte.barbero_id in barbero_comisiones_map:
            total_comisiones_dia_global += corte.precio_registrado * barbero_comisiones_map[corte.barbero_id]
            
    total_para_barberia_dia_global = total_bruto_dia_global - total_comisiones_dia_global
    
    nuevo_cierre = DiaFinalizado(
        fecha_finalizada=fecha_a_finalizar_local, # Guardar la fecha local finalizada
        total_bruto_global=total_bruto_dia_global,
        total_comisiones_barberos=total_comisiones_dia_global,
        total_para_barberia=total_para_barberia_dia_global,
        fecha_hora_cierre_efectivo=datetime.utcnow(),
        admin_id_cierre=current_user.id
    )
    db.session.add(nuevo_cierre)
    try:
        db.session.commit()
        flash(f"Día {fecha_a_finalizar_local.strftime('%d/%m/%Y')} finalizado y ventas cerradas exitosamente.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error al finalizar el día: {str(e)}", "danger")
        
    return redirect(url_for('admin_resumen_diario_barberos'))

@app.route('/admin/barberos/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_nuevo_barbero():
    if request.method == 'POST':
        nombre_usuario = request.form.get('nombre_usuario')
        nombre_completo = request.form.get('nombre_completo')
        contrasena = request.form.get('contrasena')
        rol = request.form.get('rol', 'barbero')
        
        existente = Barbero.query.filter_by(nombre_usuario=nombre_usuario).first()
        if existente:
            flash('El nombre de usuario ya existe.', 'warning')
        elif not contrasena:
            flash('La contraseña es requerida para nuevos usuarios.', 'danger')
        else:
            # El porcentaje de comisión usará el valor por defecto definido en el modelo (0.5 o 50%)
            nuevo_barbero = Barbero(
                nombre_usuario=nombre_usuario,
                nombre_completo=nombre_completo,
                rol=rol
            )
            nuevo_barbero.set_password(contrasena)
            db.session.add(nuevo_barbero)
            db.session.commit()
            flash(f'Barbero {nombre_completo} creado exitosamente.', 'success')
            return redirect(url_for('admin_barberos'))

    return render_template('admin/barbero_form.html', titulo="Nuevo Barbero", barbero=None)


@app.route('/admin/barberos/editar/<int:barbero_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_editar_barbero(barbero_id):
    barbero_a_editar = Barbero.query.get_or_404(barbero_id)
    if request.method == 'POST':
        barbero_a_editar.nombre_completo = request.form.get('nombre_completo')
        barbero_a_editar.rol = request.form.get('rol', barbero_a_editar.rol)
        
        if barbero_a_editar.nombre_usuario == 'admin' and barbero_a_editar.rol != 'admin':
            flash("No se puede cambiar el rol del administrador principal.", "warning")
            barbero_a_editar.rol = 'admin'
        
        nueva_contrasena = request.form.get('contrasena')
        if nueva_contrasena: 
            barbero_a_editar.set_password(nueva_contrasena)
        
        # Ya no procesamos el porcentaje de comisión desde aquí
        try:
            db.session.commit()
            flash(f'Barbero {barbero_a_editar.nombre_completo} actualizado.', 'success')
            return redirect(url_for('admin_barberos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar barbero: {str(e)}', 'danger')
            
    return render_template('admin/barbero_form.html', titulo=f"Editar Barbero: {barbero_a_editar.nombre_completo}", barbero=barbero_a_editar)

# Rutas para Gestión de Servicios
@app.route('/admin/servicios')
@login_required
@admin_required
def admin_servicios():
    servicios = Servicio.query.order_by(Servicio.nombre).all()
    return render_template('admin/servicios.html', servicios=servicios)

@app.route('/admin/servicios/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_nuevo_servicio():
    if request.method == 'POST':
        nombre = request.form.get('nombre_servicio')
        try:
            precio_str = request.form.get('precio_servicio')
            if not nombre or not precio_str:
                flash("Nombre y precio son requeridos.", "danger")
                raise ValueError("Campos vacíos")

            precio = float(precio_str)
            if precio < 0:
                flash("El precio no puede ser negativo.", "danger")
                raise ValueError("Precio negativo")

            existente = Servicio.query.filter_by(nombre=nombre).first()
            if existente:
                flash('Un servicio con ese nombre ya existe.', 'warning')
            else:
                nuevo_servicio = Servicio(nombre=nombre, precio=precio, activo=True)
                db.session.add(nuevo_servicio)
                db.session.commit()
                flash(f'Servicio "{nombre}" creado exitosamente.', 'success')
                return redirect(url_for('admin_servicios'))
        except ValueError as ve:
            if "Precio negativo" not in str(ve) and "Campos vacíos" not in str(ve): # No duplicar flash de precio
                 flash('Precio inválido. Debe ser un número.', 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f'Error al crear servicio: {str(e)}', 'danger')
    return render_template('admin/servicio_form.html', titulo="Nuevo Servicio", servicio=None)

@app.route('/admin/servicios/editar/<int:servicio_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_editar_servicio(servicio_id):
    servicio_a_editar = Servicio.query.get_or_404(servicio_id)
    # Obtener todos los barberos para mostrarlos en el formulario
    barberos = Barbero.query.filter_by(rol='barbero').order_by(Barbero.nombre_completo).all()

    if request.method == 'POST':
        # --- Lógica para actualizar el servicio (nombre, precio, estado) ---
        servicio_a_editar.nombre = request.form.get('nombre_servicio')
        servicio_a_editar.precio = float(request.form.get('precio_servicio'))
        servicio_a_editar.activo = 'activo' in request.form
        
        # --- Lógica NUEVA para actualizar las comisiones específicas ---
        for barbero in barberos:
            # El nombre del input en el form será, ej: 'comision_barbero_1'
            input_name = f'comision_barbero_{barbero.id}'
            submitted_porcentaje_str = request.form.get(input_name)

            if submitted_porcentaje_str: # Si se envió un valor para este barbero
                try:
                    submitted_porcentaje = float(submitted_porcentaje_str)
                    
                    # Buscar si ya existe una comisión específica
                    comision_existente = ComisionServicio.query.filter_by(
                        servicio_id=servicio_id,
                        barbero_id=barbero.id
                    ).first()

                    if comision_existente:
                        # Si existe, la actualizamos
                        comision_existente.porcentaje = submitted_porcentaje / 100.0
                    else:
                        # Si no existe, creamos una nueva
                        nueva_comision = ComisionServicio(
                            servicio_id=servicio_id,
                            barbero_id=barbero.id,
                            porcentaje=submitted_porcentaje / 100.0
                        )
                        db.session.add(nueva_comision)

                except (ValueError, TypeError):
                    flash(f"Valor de comisión inválido para {barbero.nombre_completo}. Se omitió.", "warning")

        try:
            db.session.commit()
            flash(f'Servicio "{servicio_a_editar.nombre}" y sus comisiones han sido actualizados.', 'success')
            return redirect(url_for('admin_servicios'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar: {str(e)}', 'danger')

    # Para GET, obtener las comisiones existentes para pre-llenar el formulario
    comisiones_actuales = {c.barbero_id: c.porcentaje for c in servicio_a_editar.comisiones_recibidas}

    return render_template('admin/servicio_form.html', 
                           titulo=f"Editar Servicio: {servicio_a_editar.nombre}", 
                           servicio=servicio_a_editar,
                           barberos=barberos,
                           comisiones_actuales=comisiones_actuales)

@app.route('/admin/servicios/toggle_activo/<int:servicio_id>', methods=['POST'])
@login_required
@admin_required
def admin_toggle_servicio_activo(servicio_id):
    servicio = Servicio.query.get_or_404(servicio_id)
    servicio.activo = not servicio.activo
    db.session.commit()
    estado = "activado" if servicio.activo else "desactivado"
    flash(f'Servicio "{servicio.nombre}" ha sido {estado}.', 'info')
    return redirect(url_for('admin_servicios'))


# Rutas de Reportes de Ingresos Globales
@app.route('/admin/reportes/ingresos/<periodo>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_reporte_ingresos(periodo):
    titulo_reporte = ""
    ingresos_totales = 0.0
    detalle_periodo = ""
    fecha_inicio_dt = None
    fecha_fin_dt = None
    
    # Valores por defecto para los inputs de fecha
    default_fecha_inicio_str = date.today().replace(day=1).strftime('%Y-%m-%d')
    default_fecha_fin_str = date.today().strftime('%Y-%m-%d')

    if request.method == 'POST' and periodo == 'personalizado':
        fecha_inicio_str = request.form.get('fecha_inicio')
        fecha_fin_str = request.form.get('fecha_fin')
        
        try:
            if not fecha_inicio_str or not fecha_fin_str:
                flash("Debes seleccionar ambas fechas para un reporte personalizado.", "warning")
                raise ValueError("Fechas no proveídas")

            fecha_inicio_dt = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').replace(hour=0, minute=0, second=0)
            fecha_fin_dt = datetime.strptime(fecha_fin_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)

            if fecha_inicio_dt > fecha_fin_dt:
                flash("La fecha de inicio no puede ser posterior a la fecha de fin.", "warning")
                # Mantener las fechas en el formulario para corrección
            else:
                detalle_periodo = f"Desde {fecha_inicio_dt.strftime('%d/%m/%Y')} hasta {fecha_fin_dt.strftime('%d/%m/%Y')}"
                titulo_reporte = "Ingresos Totales (Rango Personalizado)"
                query_ingresos = db.session.query(func.sum(Corte.precio_registrado)).filter( # Usar precio_registrado
                    Corte.fecha_hora_corte >= fecha_inicio_dt,
                    Corte.fecha_hora_corte <= fecha_fin_dt
                ).scalar()
                ingresos_totales = query_ingresos or 0.0
        except ValueError as ve:
            if "Fechas no proveídas" not in str(ve): # No duplicar flash
                 flash("Formato de fecha inválido. Usa AAAA-MM-DD.", "danger")
            # Mantener fechas en el formulario si es posible, o usar defaults
            fecha_inicio_str = fecha_inicio_str or default_fecha_inicio_str
            fecha_fin_str = fecha_fin_str or default_fecha_fin_str

    elif request.method == 'GET':
        if periodo == 'semanal':
            titulo_reporte = "Ingresos Totales Semanales"
            year_actual = request.args.get('year', default=date.today().isocalendar()[0], type=int)
            week_actual = request.args.get('week', default=date.today().isocalendar()[1], type=int)
            
            fecha_inicio_dt = datetime.fromisocalendar(year_actual, week_actual, 1)
            fecha_fin_dt = fecha_inicio_dt + timedelta(days=6, hours=23, minutes=59, seconds=59)
            detalle_periodo = f"Semana {week_actual}, Año {year_actual} ({fecha_inicio_dt.strftime('%d/%m/%Y')} - {fecha_fin_dt.strftime('%d/%m/%Y')})"
        
        elif periodo == 'mensual':
            titulo_reporte = "Ingresos Totales Mensuales"
            year_actual = request.args.get('year', default=date.today().year, type=int)
            month_actual = request.args.get('month', default=date.today().month, type=int)
            
            _, num_dias_mes = calendar.monthrange(year_actual, month_actual)
            fecha_inicio_dt = datetime(year_actual, month_actual, 1)
            fecha_fin_dt = datetime(year_actual, month_actual, num_dias_mes, 23, 59, 59)
            detalle_periodo = f"Mes {month_actual}, Año {year_actual}"
        
        elif periodo == 'personalizado': # Carga inicial del formulario personalizado
            titulo_reporte = "Ingresos Totales (Selecciona Rango)"
            fecha_inicio_dt = date.today().replace(day=1)
            fecha_fin_dt = date.today()
            detalle_periodo = "Por favor, selecciona un rango de fechas."
            # No calcular ingresos hasta que se envíe el formulario
        else:
            abort(404) # Periodo no válido

        if periodo == 'semanal' or periodo == 'mensual': # Calcular para predefinidos en GET
            query_ingresos = db.session.query(func.sum(Corte.precio_registrado)).filter( # Usar precio_registrado
                Corte.fecha_hora_corte >= fecha_inicio_dt,
                Corte.fecha_hora_corte <= fecha_fin_dt
            ).scalar()
            ingresos_totales = query_ingresos or 0.0

    # Para los inputs de fecha, asegurar que tengan un valor
    fecha_inicio_form_val = fecha_inicio_dt.strftime('%Y-%m-%d') if fecha_inicio_dt else default_fecha_inicio_str
    fecha_fin_form_val = fecha_fin_dt.strftime('%Y-%m-%d') if fecha_fin_dt else default_fecha_fin_str
    if request.method == 'POST' and periodo == 'personalizado': # Usar los valores del form si fue un POST
        fecha_inicio_form_val = request.form.get('fecha_inicio', default_fecha_inicio_str)
        fecha_fin_form_val = request.form.get('fecha_fin', default_fecha_fin_str)


    anios_disponibles = sorted(list(set(r[0] for r in db.session.query(extract('year', Corte.fecha_hora_corte)).distinct().all() if r[0] is not None)), reverse=True)
    current_year = date.today().year
    if not anios_disponibles or current_year not in anios_disponibles : anios_disponibles.append(current_year)
    if current_year not in anios_disponibles : anios_disponibles.sort(reverse=True)


    return render_template('admin/reporte_ingresos.html', 
                           titulo_reporte=titulo_reporte,
                           ingresos_totales=ingresos_totales,
                           periodo=periodo, 
                           detalle_periodo=detalle_periodo,
                           fecha_inicio_form=fecha_inicio_form_val,
                           fecha_fin_form=fecha_fin_form_val,
                           selected_year=year_actual if 'year_actual' in locals() else current_year,
                           selected_week=week_actual if 'week_actual' in locals() else date.today().isocalendar()[1],
                           selected_month=month_actual if 'month_actual' in locals() else date.today().month,
                           anios_disponibles=anios_disponibles
                           )


# Ruta de Liquidación Semanal para Barberos
# app.py
# (Asegúrate de tener todas las importaciones necesarias como datetime, date, timedelta, etc.)

# En tu archivo app.py, reemplaza tu función admin_liquidacion_semanal existente con esta:

# Reemplaza esta función completa en tu app.py
@app.route('/admin/liquidacion_semanal', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_liquidacion_semanal():
    year_actual = request.args.get('year', default=date.today().isocalendar()[0], type=int)
    week_actual = request.args.get('week', default=date.today().isocalendar()[1], type=int)
    
    fecha_lunes_semana = datetime.fromisocalendar(year_actual, week_actual, 1)
    fecha_sabado_semana = fecha_lunes_semana + timedelta(days=5)
    fecha_domingo_semana = fecha_lunes_semana + timedelta(days=6)

    inicio_lunes_sabado = fecha_lunes_semana.replace(hour=0, minute=0, second=0)
    fin_lunes_sabado = fecha_sabado_semana.replace(hour=23, minute=59, second=59)
    inicio_domingo = fecha_domingo_semana.replace(hour=0, minute=0, second=0)
    fin_domingo = fecha_domingo_semana.replace(hour=23, minute=59, second=59)

    periodo_id_monsat = f"{year_actual}-W{week_actual:02d}_MonSat"
    desc_periodo_monsat = f"Lunes a Sábado ({fecha_lunes_semana.strftime('%d/%m/%Y')} - {fecha_sabado_semana.strftime('%d/%m/%Y')})"
    periodo_id_domingo = f"{fecha_domingo_semana.strftime('%Y-%m-%d')}_Sun"
    desc_periodo_domingo = f"Domingo {fecha_domingo_semana.strftime('%d/%m/%Y')}"
    detalle_periodo_general = f"Liquidación para Semana ISO {week_actual}, Año {year_actual}"

    if request.method == 'POST':
        # Esta parte de la lógica para registrar pagos está bien, no la modificamos.
        barbero_id_a_pagar = request.form.get('barbero_id_pagado', type=int)
        total_bruto_pagado_str = request.form.get('total_bruto_pagado')
        monto_comision_pagado_str = request.form.get('monto_comision_pagado')
        tipo_pago = request.form.get('tipo_pago')

        periodo_pago_id_actual = ""
        desc_periodo_pago_actual = ""

        if tipo_pago == "monsat":
            periodo_pago_id_actual = periodo_id_monsat
            desc_periodo_pago_actual = desc_periodo_monsat
        elif tipo_pago == "domingo":
            periodo_pago_id_actual = periodo_id_domingo
            desc_periodo_pago_actual = desc_periodo_domingo
        else:
            flash("Tipo de pago no reconocido.", "danger")
            return redirect(url_for('admin_liquidacion_semanal', year=year_actual, week=week_actual))

        if barbero_id_a_pagar and total_bruto_pagado_str and monto_comision_pagado_str and periodo_pago_id_actual:
            try:
                total_bruto_periodo = float(total_bruto_pagado_str)
                monto_comision_periodo = float(monto_comision_pagado_str)
                pago_existente = PagoSemanal.query.filter_by(barbero_id=barbero_id_a_pagar, periodo_pago_id=periodo_pago_id_actual).first()
                if pago_existente:
                    flash(f"El pago para {desc_periodo_pago_actual} del barbero ID {barbero_id_a_pagar} ya fue registrado.", "warning")
                else:
                    nuevo_pago = PagoSemanal(barbero_id=barbero_id_a_pagar, periodo_pago_id=periodo_pago_id_actual, descripcion_periodo_pago=desc_periodo_pago_actual, total_bruto_periodo=total_bruto_periodo, monto_comision_barbero_periodo=monto_comision_periodo, fecha_pago_registrado=datetime.utcnow())
                    db.session.add(nuevo_pago)
                    db.session.commit()
                    flash(f"Pago para {desc_periodo_pago_actual} registrado para barbero ID {barbero_id_a_pagar}.", "success")
            except ValueError:
                flash("Error en los montos del pago. Deben ser números.", "danger")
            except Exception as e:
                db.session.rollback()
                flash(f"Error al registrar el pago: {str(e)}", "danger")
        else:
            flash("Información incompleta para registrar el pago.", "danger")
        return redirect(url_for('admin_liquidacion_semanal', year=year_actual, week=week_actual))

    barberos_activos = Barbero.query.filter_by(rol='barbero').order_by(Barbero.nombre_completo).all()
    liquidacion_data = []
    fechas_de_la_semana_obj = [(fecha_lunes_semana + timedelta(days=i)).date() for i in range(7)]

    for barbero in barberos_activos:
        cortes_semana = Corte.query.filter(Corte.barbero_id == barbero.id, Corte.fecha_hora_corte.between(inicio_lunes_sabado, fin_domingo)).all()
        cortes_monsat = [c for c in cortes_semana if c.fecha_hora_corte <= fin_lunes_sabado]
        cortes_domingo = [c for c in cortes_semana if c.fecha_hora_corte > fin_lunes_sabado]

        bruto_monsat = sum(c.precio_registrado for c in cortes_monsat)
        comision_monsat = sum(c.precio_registrado * get_commission_rate(barbero, c.servicio_id) for c in cortes_monsat)
        bruto_domingo = sum(c.precio_registrado for c in cortes_domingo)
        comision_domingo = sum(c.precio_registrado * get_commission_rate(barbero, c.servicio_id) for c in cortes_domingo)

        pago_monsat_registrado = PagoSemanal.query.filter_by(barbero_id=barbero.id, periodo_pago_id=periodo_id_monsat).first()
        pago_domingo_registrado = PagoSemanal.query.filter_by(barbero_id=barbero.id, periodo_pago_id=periodo_id_domingo).first()
        
        detalle_diario_completo = []
        daily_chart_data = []
        for fecha_dia_obj in fechas_de_la_semana_obj:
            cortes_del_dia = [c for c in cortes_semana if c.fecha_hora_corte.date() == fecha_dia_obj]
            bruto_dia = sum(c.precio_registrado for c in cortes_del_dia)
            comision_dia = sum(c.precio_registrado * get_commission_rate(barbero, c.servicio_id) for c in cortes_del_dia)
            daily_chart_data.append(round(bruto_dia, 2))
            detalle_diario_completo.append({'fecha': fecha_dia_obj, 'bruto_dia': bruto_dia, 'comision_dia': comision_dia, 'cantidad_cortes': len(cortes_del_dia)})
        
        liquidacion_data.append({
            'barbero': barbero,
            'monsat_bruto': bruto_monsat,
            'monsat_comision': comision_monsat,
            'monsat_barberia': bruto_monsat - comision_monsat,
            'pago_monsat_registrado': pago_monsat_registrado,
            'domingo_bruto': bruto_domingo,
            'domingo_comision': comision_domingo,
            'domingo_barberia': bruto_domingo - comision_domingo,
            'pago_domingo_registrado': pago_domingo_registrado,
            'detalle_diario': detalle_diario_completo,
            'daily_chart_labels': ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
            'daily_chart_data': daily_chart_data
        })
    
    anios_disponibles = sorted(list(set(r[0] for r in db.session.query(extract('year', Corte.fecha_hora_corte)).distinct().all() if r[0] is not None)), reverse=True)
    current_year_for_select = date.today().year
    if not anios_disponibles or current_year_for_select not in anios_disponibles:
        if current_year_for_select not in anios_disponibles : anios_disponibles.append(current_year_for_select)
        anios_disponibles.sort(reverse=True)
    
    return render_template('admin/liquidacion_semanal.html',
                           liquidacion_data=liquidacion_data,
                           detalle_periodo_general=detalle_periodo_general,
                           desc_periodo_monsat=desc_periodo_monsat,
                           desc_periodo_domingo=desc_periodo_domingo,
                           periodo_id_monsat=periodo_id_monsat,
                           periodo_id_domingo=periodo_id_domingo,
                           selected_year=year_actual,
                           selected_week=week_actual,
                           anios_disponibles=anios_disponibles,
                           fechas_de_la_semana_encabezados=fechas_de_la_semana_obj)



# --- Creación de la BD y usuario admin inicial (ejecutar una vez) ---
def inicializar_bd():
    with app.app_context():
        db.create_all()
        print("Base de datos creada.")

        admin_user = Barbero.query.filter_by(nombre_usuario='admin').first()
        if not admin_user:
            admin = Barbero(
                nombre_usuario='AdminIsabel001',
                nombre_completo='Isabel Rosas Garcia',
                rol='admin',
                porcentaje_comision=0 
            )
            admin.set_password('barberaqazxsw') 
            db.session.add(admin)
            db.session.commit()
            
        else:
            print("Usuario administrador ya existe.")

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'initdb':
        inicializar_bd()
    else:
        app.run(debug=True, host='0.0.0.0', port=5000)



# Asegúrate de que esta función esté en tu app.py
@app.route('/inicializar-base-de-datos/mi-codigo-secreto-123')
def inicializar_bd_remota():
    try:
        db.create_all()
        if not Barbero.query.filter_by(nombre_usuario='admin').first():
            admin = Barbero(nombre_usuario='admin', nombre_completo='Administrador Principal', rol='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            return "<h1>¡ÉXITO!</h1><p>Base de datos y usuario admin creados correctamente.</p>"
        else:
            return "<h1>AVISO</h1><p>La base de datos ya parece estar inicializada.</p>"
    except Exception as e:
        return f"<h1>Ocurrió un error:</h1><p>{str(e)}</p>"
    

def generar_pdf_reporte(titulo, datos, columnas, subtitulo="", totales=None):
    """Genera un PDF con formato profesional para reportes"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch)
    elements = []
    styles = getSampleStyleSheet()
    
    # Estilo personalizado para el título
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#212529'),
        spaceAfter=30,
        alignment=TA_CENTER
    )
    
    # Título principal
    elements.append(Paragraph("CORTES Y ESTILOS ISA", title_style))
    elements.append(Paragraph(titulo, styles['Heading2']))
    
    if subtitulo:
        subtitle_style = ParagraphStyle(
            'Subtitle',
            parent=styles['Normal'],
            fontSize=12,
            textColor=colors.HexColor('#6c757d'),
            spaceAfter=20,
            alignment=TA_CENTER
        )
        elements.append(Paragraph(subtitulo, subtitle_style))
    
    elements.append(Spacer(1, 0.3*inch))
    
    # Preparar datos para la tabla
    data = [columnas]  # Encabezados
    data.extend(datos)
    
    # Agregar totales si existen
    if totales:
        data.append([''] * (len(columnas) - len(totales)) + totales)
    
    # Crear tabla
    table = Table(data, repeatRows=1)
    
    # Estilo de la tabla
    style = TableStyle([
        # Encabezado
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#212529')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        
        # Cuerpo
        ('ALIGN', (0, 1), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
    ])
    
    # Si hay totales, darles formato especial
    if totales:
        style.add('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e9ecef'))
        style.add('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold')
        style.add('LINEABOVE', (0, -1), (-1, -1), 2, colors.black)
    
    table.setStyle(style)
    elements.append(table)
    
    # Pie de página con fecha
    elements.append(Spacer(1, 0.5*inch))
    fecha_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#6c757d'),
        alignment=TA_CENTER
    )
    fecha_actual = datetime.now(TARGET_TIMEZONE).strftime('%d/%m/%Y %H:%M')
    elements.append(Paragraph(f"Reporte generado el {fecha_actual}", fecha_style))
    
    # Construir PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer

def generar_excel_reporte(titulo, datos, columnas, nombre_hoja="Reporte"):
    """Genera un archivo Excel con formato profesional"""
    buffer = BytesIO()
    
    # Crear DataFrame
    df = pd.DataFrame(datos, columns=columnas)
    
    # Crear Excel writer
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        # Escribir DataFrame
        df.to_excel(writer, sheet_name=nombre_hoja, index=False, startrow=3)
        
        # Obtener la hoja de trabajo
        worksheet = writer.sheets[nombre_hoja]
        
        # Agregar título
        worksheet.merge_cells('A1:' + chr(65 + len(columnas) - 1) + '1')
        worksheet['A1'] = 'CORTES Y ESTILOS ISA'
        worksheet['A1'].font = worksheet['A1'].font.copy(size=16, bold=True)
        worksheet['A1'].alignment = worksheet['A1'].alignment.copy(horizontal='center')
        
        worksheet.merge_cells('A2:' + chr(65 + len(columnas) - 1) + '2')
        worksheet['A2'] = titulo
        worksheet['A2'].font = worksheet['A2'].font.copy(size=14, bold=True)
        worksheet['A2'].alignment = worksheet['A2'].alignment.copy(horizontal='center')
        
        # Formatear encabezados
        for cell in worksheet[4]:
            cell.font = cell.font.copy(bold=True)
            cell.fill = cell.fill.copy(start_color="212529", end_color="212529", fill_type="solid")
            cell.font = cell.font.copy(color="FFFFFF")
        
        # Ajustar ancho de columnas
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    
    buffer.seek(0)
    return buffer

# Agregar estas rutas después de las rutas existentes de reportes

@app.route('/admin/exportar/resumen_diario/<formato>')
@login_required
@admin_required
def exportar_resumen_diario(formato):
    """Exporta el resumen diario en PDF o Excel"""
    # Obtener los mismos datos que en admin_resumen_diario_barberos
    local_now = datetime.now(TARGET_TIMEZONE)
    fecha_actual_local = local_now.date()
    
    hoy_inicio_local = datetime.combine(fecha_actual_local, datetime.min.time())
    hoy_fin_local = datetime.combine(fecha_actual_local, datetime.max.time())
    
    hoy_inicio_utc = TARGET_TIMEZONE.localize(hoy_inicio_local).astimezone(pytz.utc)
    hoy_fin_utc = TARGET_TIMEZONE.localize(hoy_fin_local).astimezone(pytz.utc)
    
    barberos_activos = Barbero.query.filter_by(rol='barbero').order_by(Barbero.nombre_completo).all()
    
    datos = []
    total_bruto = 0
    total_comisiones = 0
    total_barberia = 0
    
    for barbero in barberos_activos:
        cortes_del_barbero_hoy = Corte.query.filter(
            Corte.barbero_id == barbero.id,
            Corte.fecha_hora_corte >= hoy_inicio_utc,
            Corte.fecha_hora_corte <= hoy_fin_utc
        ).all()
        
        bruto_barbero = sum(c.precio_registrado for c in cortes_del_barbero_hoy)
        comision_neta_barbero = bruto_barbero * barbero.porcentaje_comision
        para_barberia = bruto_barbero - comision_neta_barbero
        
        if bruto_barbero > 0:  # Solo incluir barberos con actividad
            datos.append([
                barbero.nombre_completo,
                len(cortes_del_barbero_hoy),
                f"${bruto_barbero:,.2f}",
                f"{barbero.porcentaje_comision:.0%}",
                f"${comision_neta_barbero:,.2f}",
                f"${para_barberia:,.2f}"
            ])
            
            total_bruto += bruto_barbero
            total_comisiones += comision_neta_barbero
            total_barberia += para_barberia
    
    columnas = ['Barbero', 'Servicios', 'Total Bruto', '% Comisión', 'Comisión Barbero', 'Para Barbería']
    totales = ['TOTALES', '', f"${total_bruto:,.2f}", '', f"${total_comisiones:,.2f}", f"${total_barberia:,.2f}"]
    
    titulo = f"Resumen Diario - {fecha_actual_local.strftime('%d/%m/%Y')}"
    
    if formato == 'pdf':
        pdf_buffer = generar_pdf_reporte(
            titulo=titulo,
            datos=datos,
            columnas=columnas,
            subtitulo="Detalle de actividad por barbero",
            totales=totales
        )
        
        response = make_response(pdf_buffer.read())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=resumen_diario_{fecha_actual_local.strftime("%Y%m%d")}.pdf'
        return response
        
    elif formato == 'excel':
        excel_buffer = generar_excel_reporte(
            titulo=titulo,
            datos=datos,
            columnas=columnas,
            nombre_hoja='Resumen Diario'
        )
        
        response = make_response(excel_buffer.read())
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        response.headers['Content-Disposition'] = f'attachment; filename=resumen_diario_{fecha_actual_local.strftime("%Y%m%d")}.xlsx'
        return response
    
    else:
        abort(404)

@app.route('/admin/exportar/liquidacion_semanal/<formato>')
@login_required
@admin_required
def exportar_liquidacion_semanal(formato):
    """Exporta la liquidación semanal en PDF o Excel"""
    year_actual = request.args.get('year', default=date.today().isocalendar()[0], type=int)
    week_actual = request.args.get('week', default=date.today().isocalendar()[1], type=int)
    
    fecha_lunes_semana = datetime.fromisocalendar(year_actual, week_actual, 1)
    fecha_sabado_semana = fecha_lunes_semana + timedelta(days=5)
    fecha_domingo_semana = fecha_lunes_semana + timedelta(days=6)
    
    inicio_lunes_sabado = fecha_lunes_semana.replace(hour=0, minute=0, second=0)
    fin_lunes_sabado = fecha_sabado_semana.replace(hour=23, minute=59, second=59)
    inicio_domingo = fecha_domingo_semana.replace(hour=0, minute=0, second=0)
    fin_domingo = fecha_domingo_semana.replace(hour=23, minute=59, second=59)
    
    barberos_activos = Barbero.query.filter_by(rol='barbero').order_by(Barbero.nombre_completo).all()
    
    datos = []
    total_bruto_semana = 0
    total_comisiones_semana = 0
    
    for barbero in barberos_activos:
        cortes_semana = Corte.query.filter(
            Corte.barbero_id == barbero.id,
            Corte.fecha_hora_corte.between(inicio_lunes_sabado, fin_domingo)
        ).all()
        
        cortes_monsat = [c for c in cortes_semana if c.fecha_hora_corte <= fin_lunes_sabado]
        cortes_domingo = [c for c in cortes_semana if c.fecha_hora_corte > fin_lunes_sabado]
        
        bruto_monsat = sum(c.precio_registrado for c in cortes_monsat)
        comision_monsat = sum(c.precio_registrado * get_commission_rate(barbero, c.servicio_id) for c in cortes_monsat)
        bruto_domingo = sum(c.precio_registrado for c in cortes_domingo)
        comision_domingo = sum(c.precio_registrado * get_commission_rate(barbero, c.servicio_id) for c in cortes_domingo)
        
        total_bruto_barbero = bruto_monsat + bruto_domingo
        total_comision_barbero = comision_monsat + comision_domingo
        
        if total_bruto_barbero > 0:
            datos.append([
                barbero.nombre_completo,
                f"${bruto_monsat:,.2f}",
                f"${comision_monsat:,.2f}",
                f"${bruto_domingo:,.2f}",
                f"${comision_domingo:,.2f}",
                f"${total_bruto_barbero:,.2f}",
                f"${total_comision_barbero:,.2f}"
            ])
            
            total_bruto_semana += total_bruto_barbero
            total_comisiones_semana += total_comision_barbero
    
    columnas = ['Barbero', 'Bruto L-S', 'Comisión L-S', 'Bruto Dom', 'Comisión Dom', 'Total Bruto', 'Total Comisión']
    totales = ['TOTALES', '', '', '', '', f"${total_bruto_semana:,.2f}", f"${total_comisiones_semana:,.2f}"]
    
    titulo = f"Liquidación Semana {week_actual} - Año {year_actual}"
    subtitulo = f"Del {fecha_lunes_semana.strftime('%d/%m/%Y')} al {fecha_domingo_semana.strftime('%d/%m/%Y')}"
    
    if formato == 'pdf':
        pdf_buffer = generar_pdf_reporte(
            titulo=titulo,
            datos=datos,
            columnas=columnas,
            subtitulo=subtitulo,
            totales=totales
        )
        
        response = make_response(pdf_buffer.read())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=liquidacion_semana_{year_actual}_{week_actual}.pdf'
        return response
        
    elif formato == 'excel':
        excel_buffer = generar_excel_reporte(
            titulo=titulo + " - " + subtitulo,
            datos=datos,
            columnas=columnas,
            nombre_hoja='Liquidación Semanal'
        )
        
        response = make_response(excel_buffer.read())
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        response.headers['Content-Disposition'] = f'attachment; filename=liquidacion_semana_{year_actual}_{week_actual}.xlsx'
        return response
    
    else:
        abort(404)

@app.route('/barbero/exportar/historial/<formato>')
@login_required
def exportar_historial_barbero(formato):
    """Exporta el historial del barbero en PDF o Excel"""
    if current_user.rol != 'barbero':
        abort(403)
    
    # Obtener parámetros del periodo
    periodo = request.args.get('periodo', 'semana')
    hoy = date.today()
    
    # Determinar rango de fechas
    if periodo == 'mes':
        start_date = hoy.replace(day=1)
        _, ultimo_dia_mes = calendar.monthrange(hoy.year, hoy.month)
        end_date = hoy.replace(day=ultimo_dia_mes)
        titulo_periodo = f"Historial del Mes - {hoy.strftime('%B %Y')}"
    elif periodo == 'personalizado':
        fecha_inicio_str = request.args.get('fecha_inicio')
        fecha_fin_str = request.args.get('fecha_fin')
        if fecha_inicio_str and fecha_fin_str:
            try:
                start_date = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
                titulo_periodo = f"Historial del {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"
            except ValueError:
                start_date = hoy - timedelta(days=hoy.weekday())
                end_date = start_date + timedelta(days=6)
                titulo_periodo = "Historial de la Semana"
        else:
            start_date = hoy - timedelta(days=hoy.weekday())
            end_date = start_date + timedelta(days=6)
            titulo_periodo = "Historial de la Semana"
    else:  # semana
        start_date = hoy - timedelta(days=hoy.weekday())
        end_date = start_date + timedelta(days=6)
        titulo_periodo = "Historial de la Semana"
    
    inicio_periodo = datetime.combine(start_date, time.min)
    fin_periodo = datetime.combine(end_date, time.max)
    
    # Obtener cortes del periodo
    cortes_periodo = Corte.query.filter(
        Corte.barbero_id == current_user.id,
        Corte.fecha_hora_corte.between(inicio_periodo, fin_periodo)
    ).order_by(Corte.fecha_hora_corte.desc()).all()
    
    # Preparar datos para exportación
    datos = []
    total_bruto = 0
    total_comision = 0
    
    for corte in cortes_periodo:
        servicio_nombre = corte.servicio_prestado.nombre if corte.servicio_prestado else "Servicio Desconocido"
        precio = corte.precio_registrado
        comision = precio * get_commission_rate(current_user, corte.servicio_id)
        
        datos.append([
            format_datetime_local(corte.fecha_hora_corte, '%d/%m/%Y'),
            format_datetime_local(corte.fecha_hora_corte, '%H:%M'),
            servicio_nombre,
            f"${precio:,.2f}",
            f"${comision:,.2f}"
        ])
        
        total_bruto += precio
        total_comision += comision
    
    columnas = ['Fecha', 'Hora', 'Servicio', 'Precio', 'Mi Comisión']
    totales = ['TOTALES', '', '', f"${total_bruto:,.2f}", f"${total_comision:,.2f}"]
    
    titulo = f"Mi Historial - {current_user.nombre_completo}"
    
    if formato == 'pdf':
        pdf_buffer = generar_pdf_reporte(
            titulo=titulo,
            datos=datos,
            columnas=columnas,
            subtitulo=titulo_periodo,
            totales=totales
        )
        
        response = make_response(pdf_buffer.read())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=mi_historial_{start_date.strftime("%Y%m%d")}_{end_date.strftime("%Y%m%d")}.pdf'
        return response
        
    elif formato == 'excel':
        excel_buffer = generar_excel_reporte(
            titulo=titulo + " - " + titulo_periodo,
            datos=datos,
            columnas=columnas,
            nombre_hoja='Mi Historial'
        )
        
        response = make_response(excel_buffer.read())
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        response.headers['Content-Disposition'] = f'attachment; filename=mi_historial_{start_date.strftime("%Y%m%d")}_{end_date.strftime("%Y%m%d")}.xlsx'
        return response
    
    else:
        abort(404)